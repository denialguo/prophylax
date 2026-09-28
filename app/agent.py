import os
import sys
import chess.pgn
import io
import json
import asyncio
from typing import AsyncGenerator
from google.adk.agents import BaseAgent, Agent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from scripts.format_narration import format_flag_for_llm
from evals.validate_narration import validate_narration, narration_violation
from hooks.sanitize_pgn import sanitize_tool_input, sanitized_movetext

async def call_mcp_tool_subprocess(tool_name: str, arguments: dict) -> dict:
    """
    Spawns the MCP server as a stdio subprocess and performs a stateless JSON-RPC call.
    """
    # PreToolUse sanitization hook
    is_valid, arguments, err_msg = sanitize_tool_input(tool_name, arguments)
    if not is_valid:
        raise ValueError(f"Input rejected by sanitization hook: {err_msg}")

    cmd = [sys.executable, "mcp_server/server.py"]
    env = dict(os.environ)
    
    # Ensure current working directory is in PYTHONPATH so the server can import modules
    cwd = os.getcwd()
    env["PYTHONPATH"] = cwd + (os.path.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env
    )
    
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    
    req_bytes = (json.dumps(req) + "\n").encode("utf-8")
    proc.stdin.write(req_bytes)
    await proc.stdin.drain()
    
    res_line = await proc.stdout.readline()
    res_str = res_line.decode("utf-8").strip()
    
    # Clean up subprocess
    proc.stdin.close()
    
    # Read remaining stderr to help diagnose crashes
    stderr_bytes = await proc.stderr.read()
    stderr_str = stderr_bytes.decode("utf-8").strip()
    
    await proc.wait()
    
    if proc.returncode != 0:
        raise RuntimeError(f"MCP server subprocess exited with code {proc.returncode}. Stderr:\n{stderr_str}")
        
    if not res_str:
        raise RuntimeError(f"MCP server subprocess closed stdout without responding. Stderr:\n{stderr_str}")
        
    res_json = json.loads(res_str)
    if "error" in res_json:
        raise RuntimeError(f"MCP tool error: {res_json['error']}")
        
    text_content = res_json["result"]["content"][0]["text"]
    return json.loads(text_content)

# Load skills instructions from SKILL.md
SKILLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.agents/skills"))

def load_skill_instruction(phase_name: str) -> str:
    path = os.path.join(SKILLS_DIR, phase_name, "SKILL.md")
    with open(path) as f:
        content = f.read()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content.strip()

# Initialize phase specialist agents — model from config (single source of truth)
from config.settings import get_narrator_model, get_fallback_narrators

NARRATOR_MODEL_NAME = get_narrator_model()  # validates at startup

opening_agent = Agent(
    name="analysing_openings",
    model=NARRATOR_MODEL_NAME,
    instruction=load_skill_instruction("opening_prep")
)

middlegame_agent = Agent(
    name="analysing_middlegames",
    model=NARRATOR_MODEL_NAME,
    instruction=load_skill_instruction("middlegame_analysis")
)

endgame_agent = Agent(
    name="analysing_endgames",
    model=NARRATOR_MODEL_NAME,
    instruction=load_skill_instruction("endgame_analysis")
)

class CoachingAgent(BaseAgent):
    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Get the latest message from user
        user_message = ""
        for event in reversed(ctx.session.events):
            if event.content and event.content.role == "user" and event.content.parts:
                user_message = event.content.parts[0].text
                break
                
        if not user_message:
            yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text="Welcome to Prophylax. Please send a PGN to analyze or ask to deep-dive a flagged move.")]))
            return

        def normalize_pgn_paste(text: str) -> str:
            import re
            text = re.sub(r'\]\s*\[', ']\n[', text)
            text = re.sub(r'\]\s+(1\.)', ']\n\n\\1', text)
            return text.strip()

        def is_likely_pgn(text: str) -> bool:
            import re
            if re.search(r'\[[A-Za-z]+\s+".*?"\]', text):
                return True
            if re.search(r'\b1\.\s*[a-zA-Z]', text):
                return True
            return False

        norm_msg = normalize_pgn_paste(user_message)
        is_pgn_intent = False
        game = None
        
        if is_likely_pgn(norm_msg):
            game = chess.pgn.read_game(io.StringIO(norm_msg))
            if game and not game.errors and len(list(game.mainline_moves())) > 0:
                is_pgn_intent = True

        try:
            if is_pgn_intent:
                max_flags = int(ctx.session.state.get("config", {}).get("max_flags", 4))
                res = await call_mcp_tool_subprocess("analyze_pgn", {"pgn": norm_msg, "max_flags": max_flags})
                flags = res.get("flags", [])
                move_evals = res.get("move_evals", [])
                summary = res.get("summary", {})

                # Rule 7: only sanitized movetext (no headers/comments) is kept for later prompts
                pgn_text = sanitized_movetext(norm_msg)
                ctx.session.state["pgn_text"] = pgn_text
                ctx.session.state["flags"] = flags
                ctx.session.state["move_evals"] = move_evals
                
                total_flags = summary.get("total_flags", 0)
                phase_dist = summary.get("phase_distribution", {})
                dist_str = ", ".join(f"{k}: {v}" for k, v in phase_dist.items() if v > 0)
                report = f"Game Report | Total Flags: {total_flags} ({dist_str})\n"
                report += "="*50 + "\n\n"
                
                explanation_depth = ctx.session.state.get("config", {}).get("explanation_depth", 1)
                audience_rating = ctx.session.state.get("config", {}).get("audience_rating", 1800)
                
                # Sort flags in game order (move number ascending, White then Black)
                sorted_flags = sorted(flags, key=lambda x: (x["move_number"], 0 if x["side"].lower() == "white" else 1))
                
                stats = {"attempted": len(sorted_flags), "passed_first": 0, "passed_retry": 0, "fallback": 0}
                
                for f in sorted_flags:
                    phase = f["phase"]
                    agent = opening_agent if phase == "opening" else (middlegame_agent if phase == "middlegame" else endgame_agent)
                        
                    prompt = format_flag_for_llm(f, explanation_depth, audience_rating)
                    narration = await self._run_sub_agent(agent, prompt)
                    
                    # Hardened validation check with retry-once-then-fallback
                    violation = narration_violation(narration, f, game)
                    if violation:
                        print(f"Validation failed for flag {f['move_number']}...{f['move_san']} ({f['side']}): {violation}. Retrying once...", file=sys.stderr, flush=True)
                        retry_prompt = (
                            f"{prompt}\n"
                            f"WARNING: Your previous response was rejected because {violation}.\n"
                            f"Please rewrite the narration, strictly obeying the NEGATIVE CONSTRAINTS."
                        )
                        narration = await self._run_sub_agent(agent, retry_prompt)
                        if not validate_narration(narration, f, game):
                            print(f"Validation failed on retry for flag {f['move_number']}...{f['move_san']}. Falling back to default narration.", file=sys.stderr, flush=True)
                            stats["fallback"] += 1
                            drop_pct = abs(f.get("wdl_delta", 0.0)) * 100
                            from scripts.format_narration import render_numbered_pv, build_header
                            pv_line = f.get("pv", [])
                            rendered_pv = render_numbered_pv(pv_line, f["move_number"], f["side"])
                            header_str = build_header(f)
                            side_cap = f["side"].capitalize()
                            
                            narration = f"Move {f['move_number']}{'.' if side_cap == 'White' else '...'}{f['move_san']} ({side_cap}) is a structural concession / error. The engine recommends the line: {rendered_pv}."
                        else:
                            stats["passed_retry"] += 1
                    else:
                        stats["passed_first"] += 1
                    
                    from scripts.format_narration import build_header
                    header = build_header(f)
                    f["narration"] = narration
                    report += f"### {header}\n\n{narration}\n"
                    report += "-"*50 + "\n\n"
                    
                print(f"Run Summary: {stats['attempted']} narrations attempted / {stats['passed_first']} passed first try / {stats['passed_retry']} passed on retry / {stats['fallback']} fell back.", file=sys.stderr, flush=True)
                ctx.session.state["report"] = report
                yield Event(
                    author=self.name,
                    content=types.Content(role="model", parts=[types.Part.from_text(text=report)]),
                    actions=EventActions(
                        state_delta={
                            "pgn_text": pgn_text,
                            "flags": flags,
                            "move_evals": move_evals,
                            "report": report
                        }
                    )
                )
                return
                
            else:
                pgn_text = ctx.session.state.get("pgn_text", "")
                if not pgn_text:
                    yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text="No game loaded in session. Please upload a PGN first.")]))
                    return
                    
                from config.settings import get_narrator_model
                from google.adk.agents import Agent
                classifier_agent = Agent(
                    name="intent_router",
                    model=get_narrator_model(),
                    instruction='''You are an intent classifier for a chess coaching assistant.
Given a user message and the current game PGN, determine if the user is asking about a SPECIFIC move in the game (e.g. "move 15", "15.Bd3", "my knight move", "11...Bxc4").
If they are asking about a specific move, identify its move number, side (white or black), and optionally the raw move SAN they typed (e.g. "Bxc4" or "Bd3").
Return ONLY a valid JSON object matching this schema exactly, with no markdown formatting:
{"is_move_query": boolean, "move_number": integer or null, "side": "white" or "black" or null, "requested_san": string or null}'''
                )
                prompt = f"User message: {user_message}\n\nGame moves:\n{pgn_text}"
                router_res = await self._run_sub_agent(classifier_agent, prompt)
                
                import json
                try:
                    router_res = router_res.strip().removeprefix("```json").removesuffix("```").strip()
                    intent = json.loads(router_res)
                except Exception:
                    intent = {"is_move_query": False}
                    
                if intent.get("is_move_query") and intent.get("move_number") and intent.get("side"):
                    game = chess.pgn.read_game(io.StringIO(pgn_text))
                    board = game.board()
                    move_num = int(intent["move_number"])
                    side_str = intent["side"].lower()
                    
                    target_ply = (move_num - 1) * 2 + (1 if side_str == "black" else 0)
                    
                    total_plies = sum(1 for _ in game.mainline_moves())
                    if target_ply < 0 or target_ply >= total_plies:
                        yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=f"Error: Move {move_num} {side_str} is outside the range of the current game.")]))
                        return
                        
                    for ply, m in enumerate(game.mainline_moves()):
                        if ply == target_ply:
                            break
                        board.push(m)
                        
                    try:
                        game_move = list(game.mainline_moves())[target_ply]
                    except IndexError:
                        yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=f"Error: Move {move_num} {side_str} is outside the range of the current game.")]))
                        return
                        
                    played_move_san = board.san(game_move)
                    
                    req_san = intent.get("requested_san")
                    if req_san:
                        import re
                        clean_req = re.sub(r'[^a-zA-Z0-9]', '', req_san).lower()
                        clean_played = re.sub(r'[^a-zA-Z0-9]', '', played_move_san).lower()
                        if clean_req and clean_req != clean_played:
                            yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=f"You asked about '{req_san}', but the move played in the game at {move_num} {side_str} was {played_move_san}. Would you like to analyze {played_move_san} instead?")]))
                            return
                            
                    fen_before = board.fen()
                    pos_analysis = await call_mcp_tool_subprocess("analyze_position", {"fen": fen_before, "multipv": 3})
                    
                    move_evals = ctx.session.state.get("move_evals", [])
                    eval_entry = next((e for e in move_evals if e["move_number"] == move_num and e["side"].lower() == side_str), None)
                    
                    if eval_entry:
                        wdl_delta = eval_entry.get("wdl_delta", 0.0)
                        phase = eval_entry.get("phase", "unknown")
                    else:
                        yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=f"Error: Could not find move evaluation data for {move_num} {side_str}.")]))
                        return
                    
                    flags = ctx.session.state.get("flags", [])
                    selected_flag = next((f for f in flags if f["move_number"] == move_num and f["side"].lower() == side_str), None)
                    
                    if selected_flag:
                        deep_dive_flag = dict(selected_flag)
                        deep_dive_flag["channel"] = "deep_dive"
                    else:
                        deep_dive_flag = {
                            "move_san": played_move_san,
                            "move_number": move_num,
                            "side": side_str,
                            "phase": phase,
                            "wdl_delta": wdl_delta,
                            "best_move_san": pos_analysis.get("multipv_lines", [{}])[0].get("pv", [""])[0],
                            "pv": pos_analysis.get("multipv_lines", [{}])[0].get("pv", []),
                            "refutation_pv": [],
                            "feature_deltas": [],
                            "concessions": {"new_weak_squares": [], "new_backward_pawns": []},
                            "channel": "deep_dive"
                        }
                    
                    from scripts.format_narration import build_header
                    header_str = build_header(deep_dive_flag)
                    deep_dive_flag["header"] = header_str
                    explanation_depth = ctx.session.state.get("config", {}).get("explanation_depth", 1)
                    audience_rating = ctx.session.state.get("config", {}).get("audience_rating", 1800)
                    explanation_prompt = format_flag_for_llm(deep_dive_flag, explanation_depth, audience_rating)
                        
                    agent = opening_agent if phase == "opening" else (middlegame_agent if phase == "middlegame" else endgame_agent)
                    explanation = await self._run_sub_agent(agent, explanation_prompt)
                    
                    # Validate output (no duplicate headers)
                    violation = narration_violation(explanation, deep_dive_flag, game)
                    if violation:
                        retry_prompt = (
                            f"{explanation_prompt}\n"
                            f"WARNING: Your previous response was rejected because {violation}. Do not hallucinate engine alternatives as the played move, and obey all negative constraints."
                        )
                        explanation = await self._run_sub_agent(agent, retry_prompt)
                        if not validate_narration(explanation, deep_dive_flag, game):
                            explanation = f"{played_move_san} was played. The engine preferred alternative is {pos_analysis.get('multipv_lines', [{}])[0].get('pv', ['Unknown'])[0]}."
                            
                    final_explanation = f"### {header_str}\n\n{explanation}"
                    yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=final_explanation)]))
                    return
                    
                else:
                    conv_agent = Agent(
                        name="conversational",
                        model=get_narrator_model(),
                        instruction="You are Prophylax, a chess coaching assistant.\nAnswer the user's question using the provided game report and flags.\nDo NOT invent new engine analysis. Only rely on the provided context."
                    )
                    report = ctx.session.state.get("report", "No report available.")
                    flags = ctx.session.state.get("flags", [])
                    conv_prompt = f"User message: {user_message}\n\nGame Report:\n{report}\n\nFlags:\n{json.dumps(flags, indent=2)}"
                    reply = await self._run_sub_agent(conv_agent, conv_prompt)
                    yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
                    return
                    
        except Exception as e:
            yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=f"Analysis failed: {str(e)}")]))

    async def _run_sub_agent(self, agent: Agent, prompt: str) -> str:
        """Run a sub-agent with automatic fallback on quota/rate-limit errors."""
        try:
            return await self._invoke_agent(agent, prompt)
        except Exception as e:
            if "ResourceExhausted" in type(e).__name__ or "429" in str(e):
                fallbacks = get_fallback_narrators(NARRATOR_MODEL_NAME)
                for fb_model in fallbacks:
                    print(
                        f"Narrator quota exhausted on {agent.model}. "
                        f"Falling back to {fb_model}.",
                        file=sys.stderr, flush=True,
                    )
                    fb_agent = Agent(
                        name=agent.name,
                        model=fb_model,
                        instruction=agent.instruction,
                    )
                    try:
                        return await self._invoke_agent(fb_agent, prompt)
                    except Exception as fb_e:
                        if "ResourceExhausted" in type(fb_e).__name__ or "429" in str(fb_e):
                            continue
                        raise
                raise  # all fallbacks exhausted
            raise

    async def _invoke_agent(self, agent: Agent, prompt: str) -> str:
        """Low-level agent invocation (no fallback logic)."""
        temp_service = InMemorySessionService()
        await temp_service.create_session(app_name="app", user_id="temp_user", session_id="temp_s")
        runner = Runner(agent=agent, app_name="app", session_service=temp_service)
        
        response_text = ""
        async for event in runner.run_async(
            user_id="temp_user",
            session_id="temp_s",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    response_text = event.content.parts[0].text
        return response_text

root_agent = CoachingAgent(name="prophylax_coach")

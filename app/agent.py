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
from evals.validate_narration import validate_narration

async def call_mcp_tool_subprocess(tool_name: str, arguments: dict) -> dict:
    """
    Spawns the MCP server as a stdio subprocess and performs a stateless JSON-RPC call.
    """
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

        # Check if the user is asking to deep-dive a flagged move
        user_msg_lower = user_message.lower()
        if "deep dive" in user_msg_lower or "explain" in user_msg_lower:
            flags = ctx.session.state.get("flags", [])
            if not flags:
                yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text="No analyzed game found in this session. Please upload a PGN first.")]))
                return
                
            selected_flag = None
            for flag in flags:
                move_san = flag["move_san"].lower()
                move_num_str = str(flag["move_number"])
                if move_san in user_msg_lower and (move_num_str in user_msg_lower or len(flags) == 1):
                    selected_flag = flag
                    break
            
            if not selected_flag:
                for flag in flags:
                    if flag["move_san"].lower() in user_msg_lower:
                        selected_flag = flag
                        break
            
            if not selected_flag:
                yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=f"Could not identify which flagged move to deep-dive. Current flags: {', '.join(str(f['move_number']) + '...' + f['move_san'] for f in flags)}")]))
                return
                
            pgn_text = ctx.session.state.get("pgn_text", "")
            if not pgn_text:
                yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text="PGN source missing from state. Cannot deep-dive.")]))
                return
                
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            board = game.board()
            target_ply = (selected_flag["move_number"] - 1) * 2
            if selected_flag["side"] == "black":
                target_ply += 1
                
            for ply, m in enumerate(game.mainline_moves()):
                if ply == target_ply:
                    break
                board.push(m)
                
            fen_before = board.fen()
            
            # Call handle_analyze_position via subprocess
            pos_analysis = await call_mcp_tool_subprocess("analyze_position", {"fen": fen_before, "multipv": 3})
            
            explanation_prompt = (
                f"User is requesting a deep dive on flagged move: {selected_flag['move_number']}...{selected_flag['move_san']}.\n"
                f"Position analysis results:\n{json.dumps(pos_analysis, indent=2)}\n"
                f"Flag data:\n{json.dumps(selected_flag, indent=2)}"
            )
            
            phase = selected_flag["phase"]
            agent = opening_agent if phase == "opening" else (middlegame_agent if phase == "middlegame" else endgame_agent)
            
            explanation = await self._run_sub_agent(agent, explanation_prompt)
            yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text=explanation)]))
            return

        # Treat user message as PGN to analyze
        try:
            pgn_text = user_message
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if not game or game.errors:
                yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part.from_text(text="Invalid PGN format or parsing error.")]))
                return
                
            res = await call_mcp_tool_subprocess("analyze_pgn", {"pgn": pgn_text, "max_flags": 4})
            flags = res.get("flags", [])
            summary = res.get("summary", {})
            
            ctx.session.state["pgn_text"] = pgn_text
            ctx.session.state["flags"] = flags
            
            # Format final report
            total_flags = summary.get("total_flags", 0)
            phase_dist = summary.get("phase_distribution", {})
            dist_str = ", ".join(f"{k}: {v}" for k, v in phase_dist.items() if v > 0)
            report = f"Game Report | Total Flags: {total_flags} ({dist_str})\n"
            report += "="*50 + "\n\n"
            
            explanation_depth = ctx.session.state.get("config", {}).get("explanation_depth", 1)
            audience_rating = ctx.session.state.get("config", {}).get("audience_rating", 1800)
            
            # Sort flags in game order (move number ascending, White then Black)
            sorted_flags = sorted(flags, key=lambda x: (x["move_number"], 0 if x["side"].lower() == "white" else 1))
            for f in sorted_flags:
                phase = f["phase"]
                agent = opening_agent if phase == "opening" else (middlegame_agent if phase == "middlegame" else endgame_agent)
                    
                prompt = format_flag_for_llm(f, explanation_depth, audience_rating)
                narration = await self._run_sub_agent(agent, prompt)
                
                # Hardened validation check with retry-once-then-fallback
                if not validate_narration(narration, f):
                    print(f"Validation failed for flag {f['move_number']}...{f['move_san']} ({f['side']}). Retrying once...", file=sys.stderr, flush=True)
                    retry_prompt = (
                        f"{prompt}\n"
                        f"WARNING: Your previous response was rejected because it mentioned invalid squares, illegal moves, "
                        f"or used forbidden phrases like 'delta of -X'.\n"
                        f"Please generate a new narration following these rules strictly:\n"
                        f"1. You must only mention squares that are in the whitelisted concessions/PV/refutation line.\n"
                        f"2. Every cited move/SAN must be fully legal and correctly numbered.\n"
                        f"3. Do not include internal feature magnitudes like 'delta of -X' or similar phrases.\n"
                        f"4. Quote the pre-rendered lines verbatim."
                    )
                    narration = await self._run_sub_agent(agent, retry_prompt)
                    
                    if not validate_narration(narration, f):
                        print(f"Validation failed on retry for flag {f['move_number']}...{f['move_san']}. Falling back to default narration.", file=sys.stderr, flush=True)
                        drop_pct = abs(f.get("wdl_delta", 0.0)) * 100
                        side_cap = f["side"].capitalize()
                        if side_cap == "White":
                            header_str = f"Move {f['move_number']}.{f['move_san']} (White): WDL drop {drop_pct:.1f}%"
                            move_str = f"Move {f['move_number']}.{f['move_san']}"
                        else:
                            header_str = f"Move {f['move_number']}...{f['move_san']} (Black): WDL drop {drop_pct:.1f}%"
                            move_str = f"Move {f['move_number']}...{f['move_san']}"
                        
                        pv_line = f.get("pv", [])
                        from scripts.format_narration import render_numbered_pv
                        rendered_pv = render_numbered_pv(pv_line, f["move_number"], f["side"])
                        
                        narration = f"{header_str}\n"
                        narration += f"{move_str} ({side_cap}) is a structural concession / error. The engine recommends the line: {rendered_pv}."
                
                report += f"{narration}\n"
                report += "-"*50 + "\n\n"
                
            yield Event(
                author=self.name,
                content=types.Content(role="model", parts=[types.Part.from_text(text=report)]),
                actions=EventActions(
                    state_delta={
                        "pgn_text": pgn_text,
                        "flags": flags
                    }
                )
            )
            
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

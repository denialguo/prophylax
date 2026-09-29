import os
import re
import sys
import chess.pgn
import io
import json
import asyncio
import contextlib
import signal
import traceback
from typing import AsyncGenerator, Optional
from google.adk.agents import BaseAgent, Agent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from scripts.format_narration import (format_flag_for_llm, build_header, render_numbered_pv, position_facts,
                                      format_claims_for_llm, claim_sentence)
from scripts.move_reference import parse_move_reference, ply_of
from evals.validate_narration import (validate_narration, narration_violation, grounding_violation, merge_flags,
                                     claims_ground, concept_violation)
from domain.claims import build_claims
from domain.convert import game_analysis_from_payload, position_analysis_from_payload, to_flag_dict
from hooks.sanitize_pgn import sanitize_tool_input, sanitized_movetext
from config.settings import get_tool_timeout
from mcp_server.server import (
    INVALID_PARAMS, INTERNAL_ERROR, ENGINE_TIMEOUT, ENGINE_CRASHED, ENGINE_UNAVAILABLE,
)

class ToolError(Exception):
    """A failed MCP tool call, carrying the server's JSON-RPC error code."""
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code

# Short, internals-free messages shown to the user; details go to stderr.
USER_MESSAGES = {
    ENGINE_TIMEOUT: "The engine timed out on this analysis. Try again, or lower STOCKFISH_NODES.",
    ENGINE_CRASHED: "The chess engine stopped unexpectedly. Please try again.",
    ENGINE_UNAVAILABLE: "The chess engine is unavailable. Check STOCKFISH_PATH and the pinned Stockfish version.",
}

OVERLOAD_RETRY_DELAY_S = 3.0

def _status(err: Exception):
    # google-genai errors carry .code, LiteLLM errors .status_code
    return getattr(err, "status_code", None) or getattr(err, "code", None)

MAX_RETRY_WAIT_S = 30.0

def _retry_delay(err: Exception) -> Optional[float]:
    """Seconds to wait before retrying the same model, or None to fall back now.
    503: a short pause. 429: only if the provider says the wait is short
    ("try again in 5.7s"); a daily quota is not worth waiting for."""
    if _status(err) == 503:
        return OVERLOAD_RETRY_DELAY_S
    m = re.search(r"try again in ([\d.]+)s", str(err))
    if _status(err) == 429 and m and float(m.group(1)) <= MAX_RETRY_WAIT_S:
        return float(m.group(1)) + 0.5
    return None

def _model_unavailable(err: Exception) -> bool:
    """Quota exhausted (429) or model overloaded (503): transient, worth another model."""
    return (_status(err) in (429, 503)
            or "ResourceExhausted" in type(err).__name__ or "429" in str(err))

def user_message_for(err: Exception) -> str:
    if _model_unavailable(err):
        return "The language model is busy or over its quota right now. Please try again in a minute."
    if isinstance(err, ToolError):
        if err.code == INVALID_PARAMS:
            return f"That input couldn't be analysed: {err}"
        return USER_MESSAGES.get(err.code, "Analysis failed due to an internal error.")
    return "Something went wrong while generating coaching. Details were logged."

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# One long-lived server per event loop, serialized by a lock. It is respawned when
# it dies, times out, or the environment it was started with has changed.
_server = None  # (proc, loop, env snapshot, lock)
_request_id = 0

def _discard_server():
    global _server
    if _server is not None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(_server[0].pid, signal.SIGKILL)
        _server = None

async def call_mcp_tool_subprocess(tool_name: str, arguments: dict) -> dict:
    """
    JSON-RPC call to the persistent MCP server subprocess (spawned on first use).
    Raises ToolError on any failure, including exceeding PROPHYLAX_TOOL_TIMEOUT_S.
    """
    global _server, _request_id
    # PreToolUse sanitization hook
    is_valid, arguments, err_msg = sanitize_tool_input(tool_name, arguments)
    if not is_valid:
        raise ToolError(INVALID_PARAMS, f"Input rejected by sanitization hook: {err_msg}")

    loop = asyncio.get_running_loop()
    env = dict(os.environ)
    if _server is not None and (_server[1] is not loop or _server[2] != env or _server[0].returncode is not None):
        _discard_server()
    if _server is None:
        cmd = [sys.executable, "mcp_server/server.py"]
        # ponytail: PYTHONPATH kept so the server runs as a script; -m would drop it
        env["PYTHONPATH"] = ROOT + (os.path.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        # Own process group, so a timeout kills the server AND its Stockfish child.
        # stderr is inherited: server logs go straight to ours.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=ROOT,
            env=env,
            start_new_session=True,
        )
        _server = (proc, loop, dict(os.environ), asyncio.Lock())
    proc, _, _, lock = _server

    async with lock:
        _request_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": _request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        timeout = get_tool_timeout()
        try:
            proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), timeout)
        except asyncio.TimeoutError:
            _discard_server()
            await proc.wait()
            raise ToolError(ENGINE_TIMEOUT, f"Tool call {tool_name} exceeded {timeout:g}s")
        except (BrokenPipeError, ConnectionResetError):
            line = b""

        if not line:
            # EOF: startup checks (path, version, limits) failed, or the server died
            _discard_server()
            await proc.wait()
            raise ToolError(ENGINE_UNAVAILABLE, f"MCP server exited with code {proc.returncode} without a response")

    res_json = json.loads(line)
    if res_json.get("id") != req["id"]:
        _discard_server()
        raise ToolError(INTERNAL_ERROR, f"Response id {res_json.get('id')} does not match request {req['id']}")
    if "error" in res_json:
        raise ToolError(res_json["error"].get("code", INTERNAL_ERROR), res_json["error"].get("message", ""))

    text_content = res_json["result"]["content"][0]["text"]
    return json.loads(text_content)

# Skills: one shared narration contract + a phase section per SKILL.md
SKILLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.agents/skills"))
PHASE_SKILLS = {"opening": "opening_prep", "middlegame": "middlegame_analysis", "endgame": "endgame_analysis"}

def _strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content.strip()

def load_skill_instruction(skill_dir: str, claims: bool = False) -> str:
    """The shared contract followed by the skill's phase section; for claim-grounded
    narration (M13), then the verified-claims rules."""
    with open(os.path.join(SKILLS_DIR, "narration_contract.md")) as f:
        contract = f.read().strip()
    with open(os.path.join(SKILLS_DIR, skill_dir, "SKILL.md")) as f:
        phase = _strip_frontmatter(f.read())
    instruction = f"{contract}\n\n{phase}"
    if claims:
        with open(os.path.join(SKILLS_DIR, "narration_contract_claims.md")) as f:
            instruction += f"\n\n{f.read().strip()}"
    return instruction

from config.settings import get_narrator_model, get_fallback_narrators, resolve_model, model_name, get_narration_input

NARRATOR_MODEL_NAME = get_narrator_model()  # validates at startup

def make_narrator(phase: str, claims: bool = False) -> Agent:
    """One narrator definition; the phase only picks the skill section and the
    trace name (analysing_openings / _middlegames / _endgames)."""
    skill_dir = PHASE_SKILLS.get(phase, "middlegame_analysis")
    name = {"opening_prep": "analysing_openings", "middlegame_analysis": "analysing_middlegames",
            "endgame_analysis": "analysing_endgames"}[skill_dir]
    return Agent(name=name, model=resolve_model(NARRATOR_MODEL_NAME), instruction=load_skill_instruction(skill_dir, claims))

NARRATORS = {phase: make_narrator(phase) for phase in PHASE_SKILLS}
CLAIM_NARRATORS = {phase: make_narrator(phase, claims=True) for phase in PHASE_SKILLS}
opening_agent, middlegame_agent, endgame_agent = NARRATORS["opening"], NARRATORS["middlegame"], NARRATORS["endgame"]

def narrator_for(phase: str, claims: bool = False) -> Agent:
    table = CLAIM_NARRATORS if claims else NARRATORS
    return table.get(phase, table["middlegame"])

NARRATION_CONCURRENCY = 4  # parallel narrator calls per game report

def _text_event(author: str, text: str, **kw) -> Event:
    return Event(author=author, content=types.Content(role="model", parts=[types.Part.from_text(text=text)]), **kw)

def _normalize_pgn_paste(text: str) -> str:
    text = re.sub(r'\]\s*\[', ']\n[', text)
    text = re.sub(r'\]\s+(1\.)', ']\n\n\\1', text)
    return text.strip()

def _is_likely_pgn(text: str) -> bool:
    return bool(re.search(r'\[[A-Za-z]+\s+".*?"\]', text) or re.search(r'\b1\.\s*[a-zA-Z]', text))

def _config(ctx: InvocationContext) -> tuple:
    cfg = ctx.session.state.get("config", {})
    return cfg.get("explanation_depth", 1), cfg.get("audience_rating", 1800)

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
            yield _text_event(self.name, "Welcome to Prophylax. Please send a PGN to analyze or ask to deep-dive a flagged move.")
            return

        norm_msg = _normalize_pgn_paste(user_message)
        game = None
        if _is_likely_pgn(norm_msg):
            game = chess.pgn.read_game(io.StringIO(norm_msg))
            if not (game and not game.errors and any(True for _ in game.mainline_moves())):
                game = None

        try:
            if game is not None:
                handler = self.analyze_game(ctx, norm_msg, game)
            else:
                pgn_text = ctx.session.state.get("pgn_text", "")
                if not pgn_text:
                    yield _text_event(self.name, "No game loaded in session. Please upload a PGN first.")
                    return
                ref = await self.route(user_message, pgn_text)
                handler = (self.ask_about_move(ctx, pgn_text, *ref) if ref
                           else self.converse(ctx, user_message, pgn_text))
            async for event in handler:
                yield event
        except Exception as e:
            print(f"Coaching turn failed: {e!r}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            yield _text_event(self.name, user_message_for(e))

    async def route(self, user_message: str, pgn_text: str) -> Optional[tuple]:
        """(move_number, side, requested_san) for a question about one move, else None.
        Deterministic parser first; the LLM router only when it finds nothing."""
        ref = parse_move_reference(user_message)
        if ref:
            return ref
        classifier_agent = Agent(
            name="intent_router",
            model=resolve_model(get_narrator_model()),
            instruction='''You are an intent classifier for a chess coaching assistant.
Given a user message and the current game PGN, determine if the user is asking about a SPECIFIC move in the game (e.g. "move 15", "15.Bd3", "my knight move", "11...Bxc4").
If they are asking about a specific move, identify its move number, side (white or black), and optionally the raw move SAN they typed (e.g. "Bxc4" or "Bd3").
Return ONLY a valid JSON object matching this schema exactly, with no markdown formatting:
{"is_move_query": boolean, "move_number": integer or null, "side": "white" or "black" or null, "requested_san": string or null}'''
        )
        router_res = await self._run_sub_agent(classifier_agent, f"User message: {user_message}\n\nGame moves:\n{pgn_text}")
        try:
            intent = json.loads(router_res.strip().removeprefix("```json").removesuffix("```").strip())
        except Exception:
            return None
        if intent.get("is_move_query") and intent.get("move_number") and intent.get("side"):
            return int(intent["move_number"]), str(intent["side"]).lower(), intent.get("requested_san")
        return None

    async def _narrate(self, f: dict, game, depth: int, rating: int, stats: dict,
                       move=None, game_id: Optional[str] = None) -> str:
        """Validated narration for one flag: retry once with the reason, then a deterministic
        fallback. With PROPHYLAX_NARRATION_INPUT=claims the narrator sees only the move's
        verified claims, and a precise term with no claim behind it is also a violation."""
        use_claims = get_narration_input() == "claims"
        if use_claims:
            claims = build_claims(move, game_id)
            ground = claims_ground(claims)
            prompt = format_claims_for_llm(f, claims, depth, rating)
            check = lambda text: narration_violation(text, ground, game) or concept_violation(text, claims)
        else:
            prompt = format_flag_for_llm(f, depth, rating)
            check = lambda text: narration_violation(text, f, game)
        agent = narrator_for(f["phase"], claims=use_claims)
        narration = await self._run_sub_agent(agent, prompt)
        violation = check(narration)
        if not violation:
            stats["passed_first"] += 1
            return narration
        print(f"Validation failed for flag {f['move_number']}...{f['move_san']} ({f['side']}): {violation}. Retrying once...", file=sys.stderr, flush=True)
        retry_prompt = (
            f"{prompt}\n"
            f"WARNING: Your previous response was rejected because {violation}.\n"
            f"Please rewrite the narration, strictly obeying the NEGATIVE CONSTRAINTS."
        )
        narration = await self._run_sub_agent(agent, retry_prompt)
        violation = check(narration)
        if not violation:
            stats["passed_retry"] += 1
            return narration
        print(f"Validation failed on retry for flag {f['move_number']}...{f['move_san']} ({violation}). Falling back to default narration.", file=sys.stderr, flush=True)
        stats["fallback"] += 1
        if use_claims:
            return " ".join(claim_sentence(c) for c in claims)
        rendered_pv = render_numbered_pv(f.get("pv", []), f["move_number"], f["side"])
        side_cap = f["side"].capitalize()
        return f"Move {f['move_number']}{'.' if side_cap == 'White' else '...'}{f['move_san']} ({side_cap}) is a structural concession / error. The engine recommends the line: {rendered_pv}."

    async def analyze_game(self, ctx: InvocationContext, norm_msg: str, game) -> AsyncGenerator[Event, None]:
        max_flags = int(ctx.session.state.get("config", {}).get("max_flags", 4))
        res = await call_mcp_tool_subprocess("analyze_pgn", {"pgn": norm_msg, "max_flags": max_flags})
        # Rule 7: only sanitized movetext (no headers/comments) is kept for later prompts
        pgn_text = sanitized_movetext(norm_msg)
        # Fails loudly (PayloadError) unless the result is well formed and matches this game
        analysis = game_analysis_from_payload(res, pgn_text)
        flags = [to_flag_dict(m) for m in analysis.flagged()]
        move_evals = res["move_evals"]
        summary = res["summary"]
        ctx.session.state["pgn_text"] = pgn_text
        ctx.session.state["flags"] = flags
        ctx.session.state["move_evals"] = move_evals

        phase_dist = summary.get("phase_distribution", {})
        dist_str = ", ".join(f"{k}: {v}" for k, v in phase_dist.items() if v > 0)
        report = f"Game Report | Total Flags: {summary.get('total_flags', 0)} ({dist_str})\n"
        report += "="*50 + "\n\n"

        depth, rating = _config(ctx)
        # Game order (move number ascending, White then Black); narrated concurrently, reported in order
        moves_by_flag = {id(f): m for f, m in zip(flags, analysis.flagged())}
        sorted_flags = sorted(flags, key=lambda x: (x["move_number"], 0 if x["side"].lower() == "white" else 1))
        stats = {"attempted": len(sorted_flags), "passed_first": 0, "passed_retry": 0, "fallback": 0}
        limit = asyncio.Semaphore(NARRATION_CONCURRENCY)

        async def bounded(f):
            async with limit:
                return await self._narrate(f, game, depth, rating, stats,
                                           move=moves_by_flag[id(f)], game_id=analysis.game_id)

        narrations = await asyncio.gather(*(bounded(f) for f in sorted_flags))
        for f, narration in zip(sorted_flags, narrations):
            f["narration"] = narration
            report += f"### {build_header(f)}\n\n{narration}\n"
            report += "-"*50 + "\n\n"

        print(f"Run Summary: {stats['attempted']} narrations attempted / {stats['passed_first']} passed first try / {stats['passed_retry']} passed on retry / {stats['fallback']} fell back.", file=sys.stderr, flush=True)
        ctx.session.state["report"] = report
        # Tracked metric: first-try / retry / fallback counts for this report, kept in the
        # session (visible in adk web and to anything reading session state)
        yield _text_event(self.name, report, actions=EventActions(state_delta={
            "pgn_text": pgn_text, "flags": flags, "move_evals": move_evals, "report": report,
            "narration_stats": stats,
        }))

    async def ask_about_move(self, ctx: InvocationContext, pgn_text: str, move_num: int, side_str: str,
                             req_san: Optional[str]) -> AsyncGenerator[Event, None]:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        mainline = list(game.mainline_moves())
        target_ply = ply_of(game.board(), move_num, side_str)
        if target_ply < 0 or target_ply >= len(mainline):
            yield _text_event(self.name, f"Error: Move {move_num} {side_str} is outside the range of the current game.")
            return

        board = game.board()
        for m in mainline[:target_ply]:
            board.push(m)
        played_move_san = board.san(mainline[target_ply])

        if req_san:
            clean_req = re.sub(r'[^a-zA-Z0-9]', '', req_san).lower()
            clean_played = re.sub(r'[^a-zA-Z0-9]', '', played_move_san).lower()
            if clean_req and clean_req != clean_played:
                yield _text_event(self.name, f"You asked about '{req_san}', but the move played in the game at {move_num} {side_str} was {played_move_san}. Would you like to analyze {played_move_san} instead?")
                return

        pos_analysis = await call_mcp_tool_subprocess("analyze_position", {"fen": board.fen(), "multipv": 3})
        position = position_analysis_from_payload(pos_analysis, board.fen())

        move_evals = ctx.session.state.get("move_evals", [])
        eval_entry = next((e for e in move_evals if e["move_number"] == move_num and e["side"].lower() == side_str), None)
        if not eval_entry:
            yield _text_event(self.name, f"Error: Could not find move evaluation data for {move_num} {side_str}.")
            return
        phase = eval_entry.get("phase", "unknown")

        top_pv = list(position.lines[0][0].moves) if position.lines else []
        flags = ctx.session.state.get("flags", [])
        selected_flag = next((f for f in flags if f["move_number"] == move_num and f["side"].lower() == side_str), None)
        if selected_flag:
            deep_dive_flag = dict(selected_flag)
        else:
            deep_dive_flag = {
                "move_san": played_move_san,
                "move_number": move_num,
                "side": side_str,
                "phase": phase,
                "wdl_delta": eval_entry.get("wdl_delta", 0.0),
                "best_move_san": top_pv[0] if top_pv else "",
                "pv": top_pv,
                "refutation_pv": [],
                "feature_deltas": {},
                "concessions": {},
            }
        deep_dive_flag["channel"] = "deep_dive"
        # Ground the deep dive in the engine data just fetched
        deep_dive_flag["alternatives"] = [list(line.moves) for line, _ in position.lines if line.moves]
        deep_dive_flag["position_facts"] = position_facts(pos_analysis["features"])

        header_str = build_header(deep_dive_flag)
        deep_dive_flag["header"] = header_str
        depth, rating = _config(ctx)
        explanation_prompt = format_flag_for_llm(deep_dive_flag, depth, rating)
        agent = narrator_for(phase)
        explanation = await self._run_sub_agent(agent, explanation_prompt)

        violation = narration_violation(explanation, deep_dive_flag, game)
        if violation:
            retry_prompt = (
                f"{explanation_prompt}\n"
                f"WARNING: Your previous response was rejected because {violation}. Do not hallucinate engine alternatives as the played move, and obey all negative constraints."
            )
            explanation = await self._run_sub_agent(agent, retry_prompt)
            if not validate_narration(explanation, deep_dive_flag, game):
                explanation = f"{played_move_san} was played. The engine preferred alternative is {top_pv[0] if top_pv else 'Unknown'}."

        yield _text_event(self.name, f"### {header_str}\n\n{explanation}")

    async def converse(self, ctx: InvocationContext, user_message: str, pgn_text: str) -> AsyncGenerator[Event, None]:
        conv_agent = Agent(
            name="conversational",
            model=resolve_model(get_narrator_model()),
            instruction="You are Prophylax, a chess coaching assistant.\nAnswer the user's question using the provided game report and flags.\nDo NOT invent new engine analysis. Only rely on the provided context."
        )
        report = ctx.session.state.get("report", "No report available.")
        flags = ctx.session.state.get("flags", [])
        conv_prompt = f"User message: {user_message}\n\nGame Report:\n{report}\n\nFlags:\n{json.dumps(flags, indent=2)}"
        reply = await self._run_sub_agent(conv_agent, conv_prompt)

        # Grounding: moves/squares must come from the stored flags or the game itself
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        board = game.board()
        played = []
        for m in game.mainline_moves():
            played.append(board.san(m))
            board.push(m)
        ground = merge_flags(flags, played)
        violation = grounding_violation(reply, ground, game)
        if violation:
            print(f"Conversational reply rejected: {violation}. Retrying once...", file=sys.stderr, flush=True)
            reply = await self._run_sub_agent(conv_agent, (
                f"{conv_prompt}\n\nWARNING: Your previous answer was rejected because {violation}. "
                f"Only cite moves and squares that appear in the report or flags above."
            ))
            if grounding_violation(reply, ground, game):
                reply = ("I can only answer from the engine analysis of this game, and I couldn't do that "
                         "for this question. Ask about a specific move (for example \"move 13 white\") "
                         "for a deep dive.")
        yield _text_event(self.name, reply)

    async def _run_sub_agent(self, agent: Agent, prompt: str) -> str:
        """Run a sub-agent. An overload (503), or a rate limit that says to wait at
        most MAX_RETRY_WAIT_S (e.g. a tokens-per-minute cap), gets one retry on the
        same model; otherwise, or if it persists, fall back through the other
        certified narrators."""
        try:
            return await self._invoke_agent(agent, prompt)
        except Exception as e:
            if not _model_unavailable(e):
                raise
            err = e
        current = model_name(agent.model)
        delay = _retry_delay(err)
        if delay is not None:
            print(f"{current} unavailable ({_status(err)}). Retrying in {delay:g}s...", file=sys.stderr, flush=True)
            await asyncio.sleep(delay)
            try:
                return await self._invoke_agent(agent, prompt)
            except Exception as e:
                if not _model_unavailable(e):
                    raise
                err = e
        for fb_model in get_fallback_narrators(current):
            print(f"{current} unavailable ({_status(err) or err}). Falling back to {fb_model}.",
                  file=sys.stderr, flush=True)
            fb_agent = Agent(name=agent.name, model=resolve_model(fb_model), instruction=agent.instruction)
            try:
                return await self._invoke_agent(fb_agent, prompt)
            except Exception as fb_e:
                if not _model_unavailable(fb_e):
                    raise
                err = fb_e
        raise err  # every certified model is unavailable

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
                    # Reasoning models return their thinking as separate thought parts
                    response_text = "".join(p.text for p in event.content.parts if p.text and not p.thought)
        return response_text

root_agent = CoachingAgent(name="prophylax_coach")

async def _run_cli(pgn_text: str, max_flags: int) -> int:
    service = InMemorySessionService()
    await service.create_session(app_name="app", user_id="cli", session_id="cli",
                                 state={"config": {"max_flags": max_flags}})
    runner = Runner(agent=root_agent, app_name="app", session_service=service)
    try:
        async for event in runner.run_async(user_id="cli", session_id="cli",
                                            new_message=types.Content(role="user", parts=[types.Part.from_text(text=pgn_text)])):
            if event.content and event.content.parts and event.content.parts[0].text:
                print(event.content.parts[0].text)
    finally:
        _discard_server()
    session = await service.get_session(app_name="app", user_id="cli", session_id="cli")
    return 0 if "report" in session.state else 1  # no report = the turn failed (message printed)

def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m app.agent", description="Prophylax game report for one PGN.")
    parser.add_argument("--pgn", required=True, help="path to a PGN file (first game is analysed)")
    parser.add_argument("--max-flags", type=int, default=4, help="maximum flagged moves to narrate (default 4)")
    args = parser.parse_args(argv)
    try:
        with open(args.pgn, encoding="utf-8", errors="replace") as f:
            pgn_text = f.read()
    except OSError as e:
        print(f"Cannot read {args.pgn}: {e.strerror}", file=sys.stderr)
        return 1
    return asyncio.run(_run_cli(pgn_text, args.max_flags))

if __name__ == "__main__":
    sys.exit(main())

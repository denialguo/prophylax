"""Claim extraction: turns the concrete statements in QUEEN's prose into typed candidates
for domain/verify.py. It reports what the text appears to claim and never whether that is
true; false claims are preserved as written. Deterministic, no LLM. Not used in production.

Extraction status (EXTRACTED / UNSUPPORTED / AMBIGUOUS / PARSE_ERROR) is not verification
status: EXTRACTED only means "this is what the sentence says".

Every fact is bound to a board: the line of moves the prose has reached at that point, as a
replay prefix from the root. When that cannot be resolved uniquely the prefix is None and the
fact must not be judged. python-chess is used here for one purpose only, attaching a mentioned
move to its place in a line; truth is left to the verifier.

Facts carry two safety fields a judge must honour:
  guards       identities the sentence states ("the pawn on g5"); `attacks` alone would accept
               an empty g5
  change_from  for "creates" and "opens": a fact that must not already have held before the
               line's last move (the e2 pawn is passed before e2-e4 "creates a passed pawn")"""
import re
from typing import Literal, Optional

import chess

from domain.models import _Model
from domain.verify import Fact, Ply

ExtractionStatus = Literal["EXTRACTED", "UNSUPPORTED", "AMBIGUOUS", "PARSE_ERROR"]

SQ = r"[a-h][1-8]"
PIECE = r"pawn|knight|bishop|rook|queen|king"
LETTER = {"pawn": "", "knight": "N", "bishop": "B", "rook": "R", "queen": "Q", "king": "K"}
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "1": 1, "2": 2, "3": 3, "4": 4}

_NUM = r"(?:(\d{1,3})\s*(\.\.\.|…|\.)\s*)?"
_VERBOSE = (rf"(white|black) ({PIECE}) ({SQ})(?:-({SQ})| takes (?:white|black) ({PIECE}) ({SQ}))"
            r"(?:(?:white|black) (queen|rook|bishop|knight))?([+#])?")
MOVE = re.compile(_NUM + r"\b" + _VERBOSE)
_SAN_TOKEN = r"(?:O-O-O|O-O|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h](?:x[a-h])?[1-8](?:=[QRBN])?)[+#]?"
SAN_MOVE = re.compile(rf"\b(\d{{1,3}})\s*(\.\.\.|…|\.)\s*({_SAN_TOKEN})(?:\s+({_SAN_TOKEN})(?![\w-]))?")
_BROKEN = re.compile(rf"\b(?:white|black) (?:{PIECE}) [a-z]?\d*[a-z]*\d*-\S*")
_HEADER = re.compile(r"^(I{1,3}|IV|V)\.(?:([a-z])\.)?\s")
_KEYS = ("BEST_MOVE:", "CRITICAL_LINE:", "PROMISING_MOVES:", "EVALUATION:")
SHORT_HEADER = 120  # a header paragraph this short has its body in the next paragraph

# A clause with any of these is not a positive statement of fact about the board
_NOT_ASSERTED = re.compile(r"\b(not|cannot|can't|can|could|would|might|may|must|should|never|unless|without|fails?|"
                           r"unable|potential|plan|plans|avoid|avoids|avoiding)\b", re.I)
_STRATEGIC = re.compile(r"\b(trap\w*|only (?:drawing |saving )?move|prophyla\w+|coordinat\w+|initiative|dominat\w+|seiz\w+|"
                        r"pressur\w+|activ\w+|space|better endgame|compensation|strong outpost|practical|zugzwang|"
                        r"opposition|tempo|fixed target|liability|en prise|win\w*|forced mate|mate in|deficit|eye\w*)\b", re.I)
# Concrete vocabulary with no complete pattern: it may be a claim, but its terms are unresolved
_CONCRETE = re.compile(r"\b(attack\w*|target\w*|pin\w*|block\w*|passed|passer|open file|open long|controll?\w*|covered|"
                       r"defend\w*|undefended|guard\w*|checkmate)\b", re.I)
_NOT_CONCRETE = re.compile(r"\b(passed-pawn|pressure|en prise|hemmed|eye\w*|capture with check)\b", re.I)
_PLANNED = re.compile(r"\b(plan|plans|practical question|key strategic question)\b", re.I)
# A move after these is an idea, not a step in the line
_HYPOTHETICAL = re.compile(r"\b(threat of|threatening|threatens|prepar\w+|to meet|latent|the devastating|the check)\b", re.I)
_FORK = re.compile(r"\b(alternative|instead|rather)\b", re.I)


class _Span(_Model):
    source_id: str = ""
    sentence: str
    span_start: int
    span_end: int


class ExtractedFact(_Span):
    status: ExtractionStatus
    fact: Optional[Fact] = None
    guards: tuple[Fact, ...] = ()
    change_from: Optional[Fact] = None
    prefix: Optional[tuple[str, ...]] = None    # UCI moves from the root; None = no board resolved
    reason: Optional[str] = None


class ExtractedVariation(_Span):
    status: ExtractionStatus
    kind: Literal["critical_line", "best_move", "promising_move", "prose", "text"]
    plies: tuple[Ply, ...] = ()
    ucis: tuple[str, ...] = ()                  # as written, even where the move is illegal
    reason: Optional[str] = None


class Extraction(_Model):
    source_id: str
    root_fen: str
    facts: tuple[ExtractedFact, ...] = ()
    variations: tuple[ExtractedVariation, ...] = ()


# ── moves ────────────────────────────────────────────────────────────────────
class _Mention:
    """One move as the text states it. `start`/`end` locate it in the source."""
    def __init__(self, start, end, number, dots, color, piece, source, target, captured, promotion, suffix, san=None):
        self.start, self.end, self.number, self.dots = start, end, number, dots
        self.color, self.piece, self.source, self.target = color, piece, source, target
        self.captured, self.promotion, self.suffix, self.san = captured, promotion, suffix, san

    def text_san(self) -> str:
        """Long-form SAN: keeps the stated source square, and the verifier's parser reads it."""
        if self.san:
            return self.san
        if self.piece == "king" and self.source[0] == "e" and self.target[0] in "gc" and self.source[1] == self.target[1]:
            return ("O-O" if self.target[0] == "g" else "O-O-O") + (self.suffix or "")
        promo = f"={LETTER[self.promotion]}" if self.promotion else ""
        return f"{LETTER[self.piece]}{self.source}{'x' if self.captured else ''}{self.target}{promo}{self.suffix or ''}"

    def ply(self) -> Ply:
        if self.san:
            return Ply(san=self.san)
        captures = self.captured if self.captured != "king" else None
        return Ply(san=self.text_san(), side=self.color, piece=self.piece, captures=captures or "none")

    def uci(self, board: Optional[chess.Board] = None) -> str:
        if self.source:
            return f"{self.source}{self.target}{LETTER[self.promotion].lower() if self.promotion else ''}"
        try:
            return board.parse_san(self.san).uci() if board else self.san
        except ValueError:
            return self.san


def _verbose_mentions(text: str, offset: int = 0) -> list:
    out = []
    for m in MOVE.finditer(text):
        number, dots, color, piece, src, quiet_to, captured, take_to, promotion, suffix = m.groups()
        out.append(_Mention(offset + m.start(), offset + m.end(), int(number) if number else None, dots, color, piece,
                            src, quiet_to or take_to, captured, promotion, suffix))
    return out


def _san_mentions(text: str, taken: list, offset: int = 0) -> list:
    out = []
    for m in SAN_MOVE.finditer(text):
        s, e = offset + m.start(), offset + m.end()
        if any(s < t.end and t.start < e for t in taken):
            continue
        number, dots, san, reply = m.groups()
        black = dots != "."
        first_end = offset + m.end(3)
        out.append(_Mention(s, first_end, int(number), dots, "black" if black else "white", None, None, None, None, None, None, san))
        if reply:
            out.append(_Mention(first_end, e, int(number) + (1 if black else 0), "." if black else "...",
                                "white" if black else "black", None, None, None, None, None, None, reply))
    return out


class _Line:
    """The line the prose has reached, from the root. Attaching a mention uses its number
    (numbers count from the root position), then legality; several possible places and no
    number means the branch point is not stated, and the line stops being resolved."""
    def __init__(self, root: chess.Board, path=()):
        self.root, self.path, self.plies = root, list(path), []
        self.dead = False          # no board can be named until a numbered move re-anchors
        self.ambiguous = False
        self.fresh = False
        self.parent_len = len(self.path)
        self.tail = []             # an unplayable mention kept for the variation
        self.first = True

    def board(self, upto=None) -> chess.Board:
        b = self.root.copy()
        for mv, _ in self.path[:upto]:
            b.push(mv)
        return b

    def _ply_index(self, m) -> int:
        black = m.color == "black"
        return (2 * (m.number - 1) + (1 if black else 0)) - (1 if not self.root.turn else 0)

    def _move(self, board, m) -> Optional[chess.Move]:
        if (board.turn == chess.WHITE) != (m.color == "white"):
            return None
        try:
            return board.parse_san(m.text_san())
        except ValueError:
            return None

    def _places(self, m, upto) -> list:
        return [k for k in range(upto, -1, -1) if self._move(self.board(k), m)]

    def attach(self, m, fork_cue: bool, adjacent: bool, emit) -> None:
        if m.number is not None:
            index = self._ply_index(m)
            if index < 0 or index > len(self.path):
                self._stop(emit, ambiguous=not self.dead)
                return
            if self.dead:
                self.dead, self.tail = False, []
            self._place(m, index, emit)
            return
        if self.dead:
            if not fork_cue:
                return
            self.dead, self.tail = False, []
        if self.first and self.parent_len:   # a sub-variation: where in the parent line?
            places = self._places(m, self.parent_len)
            if len(places) == 1:
                self._place(m, places[0], emit)
            elif places:
                self._stop(emit, ambiguous=True)
            else:
                self._place(m, len(self.path), emit)
            return
        if fork_cue:
            colors = [k for k, (mv, _) in enumerate(self.path) if (self.board(k).turn == chess.WHITE) == (m.color == "white")]
            if colors and self._move(self.board(colors[-1]), m):
                self._place(m, colors[-1], emit)
                return
        if adjacent or self._move(self.board(), m):   # written straight after the last move: it continues
            self._place(m, len(self.path), emit)
            return
        places = self._places(m, len(self.path))
        if len(places) == 1:
            self._place(m, places[0], emit)
        elif places:
            self._stop(emit, ambiguous=True)
        else:
            self._place(m, len(self.path), emit)

    def _place(self, m, index, emit) -> None:
        board = self.board(index)
        move = self._move(board, m)
        if index < len(self.path):
            if move and self.path[index][0] == move:   # the text restating a move already in the line
                self.path = self.path[:index + 1]
                self.first = False
                return
            if self.fresh:
                emit(self)
            self.path = self.path[:index]
            self.parent_len = min(self.parent_len, index)
        self.first = False
        if move is None or board.is_game_over():       # kept as written: the verifier says what is wrong
            self.tail = [m]
            self.fresh = True
            emit(self)
            self.fresh, self.dead, self.tail = False, True, []
            return
        self.path.append((move, m))
        self.fresh = True

    def _stop(self, emit, ambiguous=False) -> None:
        if self.fresh:
            emit(self)
        self.fresh = False
        self.dead, self.ambiguous = True, self.ambiguous or ambiguous

    def prefix(self) -> Optional[tuple]:
        return None if self.dead else tuple(mv.uci() for mv, _ in self.path)


# ── references ───────────────────────────────────────────────────────────────
_COLOR = r"\b(?:(?:[Tt]he|[Aa]n?)\s+)?(?:(White|Black)'s\s+)?(?:(?:the\s+)?(white|black)\s+)?"
REF = re.compile(_COLOR + rf"(?:(?!white\b|black\b)[a-z-]+\s+){{0,2}}?({PIECE})\s+on\s+({SQ})\b")
REF_SHORT = re.compile(rf"\b({SQ})\s+pawn\b")
NAMED = re.compile(rf"\b(?:the|a)\s+(?:(?:checking|moved|attacking)\s+)?({PIECE})\b", re.I)
MOVES_TO = re.compile(rf"\bthe\s+({PIECE})\s+[a-z]+\s+to\s+({SQ})\b", re.I)


def _ref(m) -> dict:
    return {"color": (m.group(2) or (m.group(1) or "").lower()) or None, "piece": m.group(3), "square": m.group(4)}


def _targets(text: str) -> tuple:
    """(targets, characters read): the things named at the start of `text`."""
    out, pos = [], 0
    item = re.compile(rf"\s*(?:,\s*)?(?:and\s+|both\s+)?(?:{REF.pattern}|({SQ})\b(?!-))")
    while True:
        m = item.match(text, pos)
        if not m:
            break
        groups = m.groups()
        if groups[3]:
            out.append({"color": (groups[1] or (groups[0] or "").lower()) or None, "piece": groups[2], "square": groups[3]})
        else:
            out.append({"color": None, "piece": None, "square": groups[4]})
        pos = m.end()
    return out, pos


def _guards(ref: dict) -> list:
    if not ref.get("piece"):
        return []
    args = {"square": ref["square"], "piece": ref["piece"]}
    if ref.get("color"):
        args["color"] = ref["color"]
    return [Fact(predicate="piece_at", args=args)]


# ── one source text ──────────────────────────────────────────────────────────
class _Extractor:
    def __init__(self, text: str, root_fen: str, source_id: str):
        self.text, self.source_id = text, source_id
        self.root_fen, self.root = root_fen, chess.Board(root_fen)
        self.facts, self.variations = [], []

    # output
    def _fact(self, sentence, start, end, status, fact=None, guards=(), change_from=None, prefix=None, reason=None):
        self.facts.append(ExtractedFact(source_id=self.source_id, sentence=sentence, span_start=start, span_end=end,
                                        status=status, fact=fact, guards=tuple(guards), change_from=change_from,
                                        prefix=prefix, reason=reason))

    def _emit_line(self, line: _Line) -> None:
        mentions = [m for _, m in line.path] + line.tail
        fresh_from = line.parent_len if line.parent_len <= len(mentions) else 0
        if not mentions[fresh_from:]:
            return
        start, end = mentions[fresh_from].start, mentions[-1].end
        ucis = [mv.uci() for mv, _ in line.path] + [m.uci(line.board()) for m in line.tail]
        self.variations.append(ExtractedVariation(
            source_id=self.source_id, sentence=self.text[start:end], span_start=start, span_end=end, status="EXTRACTED",
            kind="prose", plies=tuple(m.ply() for m in mentions), ucis=tuple(ucis)))

    def _ambiguous_line(self, m, reason) -> None:
        self.variations.append(ExtractedVariation(source_id=self.source_id, sentence=self.text[m.start:m.end],
                                                  span_start=m.start, span_end=m.end, status="AMBIGUOUS", kind="prose",
                                                  reason=reason))

    # structure
    def run(self) -> Extraction:
        if not self.text.strip():
            self._fact("", 0, 0, "PARSE_ERROR", reason="empty text")
            return self._result()
        pos, paragraphs = 0, []
        for chunk in re.split(r"(\n\s*\n)", self.text):
            paragraphs.append((pos, chunk))
            pos += len(chunk)
        last_header_short = False
        line, lines = _Line(self.root), {}
        in_summary = False
        for start, para in paragraphs:
            if not para.strip():
                continue
            for sub_start, sub in self._split_keys(start, para):
                key = next((k for k in _KEYS if sub.startswith(k)), None)
                if key:
                    self._flush(line)
                    self._structured(key, sub_start, sub)
                    continue
                body_start, body = sub_start, sub
                if body.startswith("ANALYSIS:"):
                    body_start, body = sub_start + 9, sub[9:]
                header = _HEADER.match(body.lstrip())
                if header:
                    self._flush(line)
                    roman, letter = header.groups()
                    parent = lines.get(roman) if letter else None
                    if letter and parent is None:
                        line = _Line(self.root)
                        line.dead = True
                    elif letter:
                        line = _Line(self.root, parent.path)
                        if parent.dead and not parent.path:
                            line.dead = True
                    else:
                        line = _Line(self.root)
                        lines[roman] = line
                    in_summary = False
                    last_header_short = len(body) < SHORT_HEADER
                elif lines and not last_header_short:
                    self._flush(line)
                    in_summary = True
                else:
                    last_header_short = False
                self._prose(body_start, body, line, in_summary)
        self._flush(line)
        return self._result()

    def _split_keys(self, start, para):
        pieces, pos = [], 0
        for m in re.finditer(rf"^(?:{'|'.join(_KEYS)})", para, re.M):
            if m.start() > pos:
                pieces.append((start + pos, para[pos:m.start()]))
            pos = m.start()
        pieces.append((start + pos, para[pos:]))
        return [(s, p) for s, p in pieces if p.strip()]

    def _flush(self, line: _Line) -> None:
        if line.fresh:
            self._emit_line(line)
            line.fresh = False

    def _structured(self, key, start, text) -> None:
        line_end = text.find("\n")
        body = text if line_end < 0 else text[:line_end]
        value_at = len(key)
        mentions = _verbose_mentions(body[value_at:], start + value_at)
        if key == "CRITICAL_LINE:":
            self._whole_line(body, start, mentions, "critical_line")
        elif key in ("BEST_MOVE:", "PROMISING_MOVES:"):
            kind = "best_move" if key == "BEST_MOVE:" else "promising_move"
            for m in mentions:
                self._single(m, kind)
        else:
            self._fact(body, start, start + len(body), "UNSUPPORTED", reason="engine-style evaluation")

    def _whole_line(self, body, start, mentions, kind) -> None:
        board, ucis = self.root.copy(), []
        for m in mentions:
            ucis.append(m.uci(board))
            mv = _Line(self.root)._move(board, m)
            if mv:
                board.push(mv)
        self.variations.append(ExtractedVariation(source_id=self.source_id, sentence=body, span_start=start,
                                                  span_end=start + len(body), status="EXTRACTED" if mentions else "PARSE_ERROR",
                                                  kind=kind, plies=tuple(m.ply() for m in mentions), ucis=tuple(ucis)))

    def _single(self, m, kind) -> None:
        self.variations.append(ExtractedVariation(source_id=self.source_id, sentence=self.text[m.start:m.end], span_start=m.start,
                                                  span_end=m.end, status="EXTRACTED", kind=kind, plies=(m.ply(),),
                                                  ucis=(m.uci(self.root),)))

    # prose
    def _prose(self, start, body, line: _Line, in_summary: bool) -> None:
        mentions = _verbose_mentions(body, start)
        mentions += _san_mentions(body, mentions, start)
        mentions.sort(key=lambda m: m.start)
        masked = list(body)
        for m in mentions:
            for i in range(m.start - start, m.end - start):
                if masked[i] != "\n":
                    masked[i] = "■"
        masked = "".join(masked)
        header = _HEADER.match(masked.lstrip())
        pos = (masked.find(header.group(0)) + len(header.group(0))) if header else 0
        for s, e in self._sentences(masked, pos):
            self._sentence(start, masked, s, e, mentions, line, in_summary)

    def _sentences(self, masked, pos):
        out, s = [], pos
        for m in re.finditer(r"[.!?](?=\s+[\w(■\[]|\s*$)|\n", masked[pos:]):
            e = pos + m.end()
            if masked[s:e].strip():
                out.append((s, e))
            s = e
        if masked[s:].strip():
            out.append((s, len(masked)))
        return out

    def _sentence(self, start, masked, s, e, mentions, line: _Line, in_summary: bool) -> None:
        here = sorted((m for m in mentions if start + s <= m.start < start + e), key=lambda m: m.start)
        sentence = self.text[start + s:start + e]
        before_all = line.path[-1][1] if line.path and not line.dead and not in_summary else None
        ctx = {"subject": None, "subject_at": -1, "last": before_all, "last_at": -1, "line": line,
               "hypothetical": False, "in_summary": in_summary, "planned": bool(_PLANNED.search(masked[s:e]))}
        moves = iter(here)
        pending, read_to, idea = next(moves, None), s, False
        for cs, ce in self._clauses(masked, s, e):
            while pending is not None and pending.start - start < ce:
                between = masked[read_to:pending.start - start]
                read_to = pending.end - start
                if idea or _HYPOTHETICAL.search(between):
                    idea = True
                    self._fact(sentence, pending.start, pending.end, "UNSUPPORTED",
                               reason="a hypothetical move, not a step in the line")
                elif not in_summary:
                    was_ambiguous = line.ambiguous
                    line.attach(pending, bool(_FORK.search(between)), not between.strip(), self._emit_line)
                    if line.ambiguous and not was_ambiguous:
                        self._ambiguous_line(pending, "the text does not state where this move branches from")
                    ctx["last"], ctx["last_at"] = (None, -1) if line.dead else (pending, pending.start)
                pending = next(moves, None)
            self._clause(start, masked, sentence, cs, ce, ctx)
            ctx["hypothetical"] = idea   # what a clause says before an idea starts still has its board

    def _clauses(self, masked, s, e):
        out, cs = [], s
        for m in re.finditer(r"[,;:]\s|\s[—–]\s|—|\(|\)|\s+and\s+(?=[a-z]+ing\b)", masked[s:e]):
            ce = s + m.start()
            if masked[cs:ce].strip():
                out.append((cs, ce))
            cs = s + m.end()
        if masked[cs:e].strip():
            out.append((cs, e))
        return out

    # one clause
    def _clause(self, start, masked, sentence, cs, ce, ctx) -> None:
        clause = masked[cs:ce]
        a, b = start + cs, start + ce
        if _BROKEN.search(clause):
            self._fact(sentence, a, b, "PARSE_ERROR", reason="a move in QUEEN notation with unreadable squares")
            return
        negated = _NOT_ASSERTED.search(clause)
        self._subject(clause, cs, ctx)
        found = []
        if not negated and not ctx["planned"]:
            for pattern in (self._attacks, self._pins, self._blocks, self._files, self._passed, self._promotion,
                            self._state, self._placement):
                found += pattern(clause, ctx, masked, cs)
        prefix = self._board(ctx)
        for fact, guards, change, (fs, fe) in found:
            self._fact(sentence, a + fs, a + fe, "EXTRACTED", fact=fact, guards=guards, change_from=change, prefix=prefix,
                       reason=None if prefix is not None else "no board resolved for this clause")
        if found:
            rest = list(clause)
            for *_, (fs, fe) in found:
                rest[fs:fe] = " " * (fe - fs)
            left = _CONCRETE.search("".join(rest))
            if left and not _NOT_CONCRETE.search(clause):
                self._fact(sentence, a + left.start(), a + left.end(), "AMBIGUOUS", reason="concrete vocabulary with unresolved terms")
            return
        if negated:
            self._fact(sentence, a, b, "UNSUPPORTED", reason=f"not asserted as fact ('{negated.group(1)}')")
        elif ctx["planned"]:
            self._fact(sentence, a, b, "UNSUPPORTED", reason="a stated plan, not a board fact")
        elif _CONCRETE.search(clause) and not _NOT_CONCRETE.search(clause):
            self._fact(sentence, a, b, "AMBIGUOUS", reason="concrete vocabulary with unresolved terms")
        elif _STRATEGIC.search(clause):
            self._fact(sentence, a, b, "UNSUPPORTED", reason="strategic language")
        elif "\u25a0" not in clause:   # a clause that only gives moves belongs to the variation, not here
            self._fact(sentence, a, b, "UNSUPPORTED", reason="no supported pattern")

    def _board(self, ctx) -> Optional[tuple]:
        if ctx["hypothetical"] or ctx["in_summary"]:
            return None
        return ctx["line"].prefix()

    def _subject(self, clause, cs, ctx) -> None:
        moved = MOVES_TO.search(clause)
        if moved:
            ctx["subject"] = {"color": None, "piece": moved.group(1).lower(), "square": moved.group(2)}
            ctx["subject_at"] = cs + moved.start()
            return
        m = REF.search(clause)   # only a piece named before any verb is the subject, not its target
        verb = re.search(rf"\b(?:{'|'.join(self.VERBS)}|pins?|blocks|blocking)\b", clause)
        if m and ctx["subject"] is None and not (verb and verb.start() < m.start()):
            ctx["subject"] = _ref(m)
            ctx["subject_at"] = cs + m.start()

    def _source(self, ctx) -> Optional[dict]:
        """Who a subject-less "attacking X" is about: the nearer of the sentence's subject and
        the last move."""
        last = ctx["last"]
        if last is not None and last.source and ctx.get("last_at", -1) >= ctx["subject_at"]:
            return {"color": last.color, "piece": last.piece, "square": last.target}
        if ctx["subject"]:
            return ctx["subject"]
        if last is not None and last.source:
            return {"color": last.color, "piece": last.piece, "square": last.target}
        return None

    def _named(self, piece: str, ctx) -> Optional[dict]:
        """"the knight": the piece that just moved, if it is that kind; otherwise unresolved."""
        if ctx["subject"] and ctx["subject"]["piece"] == piece:
            return ctx["subject"]
        last = ctx["last"]
        if last is not None and last.piece == piece:
            return {"color": last.color, "piece": piece, "square": last.target}
        return None

    def _relation(self, predicate, source, targets, span) -> list:
        out = []
        for t in targets:
            args = {"source": source["square"], "target": t["square"]}
            if source.get("piece"):
                args["piece"] = source["piece"]
            guards = _guards(t)
            if source.get("color") and source.get("piece"):
                guards = _guards(source) + guards
            out.append((Fact(predicate=predicate, args=args), guards, None, span))
        return out

    VERBS = {"attacks": "attacks", "attacking": "attacks", "attack": "attacks", "threatens": "attacks",
             "threatening": "attacks", "controls": "attacks", "controlling": "attacks", "covers": "attacks",
             "covering": "attacks", "defends": "defends", "defending": "defends", "guards": "defends",
             "guarding": "defends", "protects": "defends", "protecting": "defends"}
    PASSIVE = {"attacked": "attacks", "controlled": "attacks", "covered": "attacks", "defended": "defends",
               "guarded": "defends", "protected": "defends"}

    def _attacks(self, clause, ctx, masked, cs) -> list:
        out = []
        adv = r"(?:(?:directly|immediately|now|also|currently|already|simultaneously|both)\s+)*"
        verbs = "|".join(self.VERBS)
        for m in re.finditer(rf"\b{adv}({verbs})\b", clause):
            predicate = self.VERBS[m.group(1)]
            targets, read = _targets(clause[m.end():])
            if not targets:
                continue
            source = self._attacker(clause[:m.start()], ctx)
            if source is not None:
                out += self._relation(predicate, source, targets, (m.start(), m.end() + read))
        for m in re.finditer(rf"((?:{SQ})(?:\s+and\s+{SQ})*)\s+(?:is|are)\s+(?:both\s+)?(attacked|controlled|covered|defended|guarded|protected)\s+by\s+", clause):
            by = REF.match(clause, m.end())
            if by:
                out += self._relation(self.PASSIVE[m.group(2)], _ref(by),
                                      [{"square": s, "piece": None} for s in re.findall(SQ, m.group(1))], (m.start(), by.end()))
        for m in re.finditer(rf"\badding a \w+ attacker on ({SQ})\b", clause):
            source = self._source(ctx)
            if source:
                out += self._relation("attacks", source, [{"square": m.group(1), "piece": None}], m.span())
        return out + self._parenthetical(masked, cs, clause, ctx)

    def _attacker(self, head, ctx) -> Optional[dict]:
        """Who the verb in this clause is about, from the words before it."""
        refs = list(REF.finditer(head))
        if refs and head[refs[-1].end():].strip() in ("", "that", "which"):
            return _ref(refs[-1])
        side = re.search(r"\b(White|Black)\s*$", head)
        if side:
            last = ctx["last"]
            if last is not None and last.color == side.group(1).lower() and last.source:
                return {"color": last.color, "piece": last.piece, "square": last.target}
            return None
        named = re.search(rf"\bthe\s+({PIECE})\s*$", head, re.I)
        return self._named(named.group(1).lower(), ctx) if named else self._source(ctx)

    def _parenthetical(self, masked, cs, clause, ctx) -> list:
        """"f2 (attacked by the queen)": the square sits just before the bracket."""
        out = []
        before = masked[max(0, cs - 6):cs]
        square = re.search(rf"({SQ})\s*\($", before)
        if square:
            m = re.match(rf"\s*attacked by the ({PIECE})\s*$", clause)
            if m:
                source = self._named(m.group(1), ctx)
                if source:
                    out += self._relation("attacks", source, [{"square": square.group(1), "piece": None}], m.span())
        return out

    def _pins(self, clause, ctx, masked, cs) -> list:
        out = []
        for m in REF.finditer(clause):
            tail = re.match(r"\s+(?:pins?|immobilizes)\s+", clause[m.end():])
            if not tail:
                continue
            pos = m.end() + tail.end()
            pinned = REF.match(clause, pos)
            if not pinned:
                continue
            against = re.match(r"\s+(?:against|to)\s+", clause[pinned.end():])
            if not against:
                continue
            target = REF.match(clause, pinned.end() + against.end())
            if target:
                refs = [_ref(m), _ref(pinned), _ref(target)]
                out.append((Fact(predicate="pinned_against", args={"pinner": refs[0]["square"], "pinned": refs[1]["square"],
                                                                   "target": refs[2]["square"]}),
                            [g for r in refs for g in _guards(r)], None, (m.start(), target.end())))
        return out

    def _blocks(self, clause, ctx, masked, cs) -> list:
        out = []
        for m in REF.finditer(clause):
            active = re.match(r"\s+blocks\s+", clause[m.end():])
            passive = re.match(r"\s+is\s+blocked\s+by\s+", clause[m.end():])
            if active:
                other = REF.match(clause, m.end() + active.end())
                if other:
                    out.append(self._block(_ref(other), _ref(m), (m.start(), other.end())))
            elif passive:
                other = REF.match(clause, m.end() + passive.end())
                if other:
                    out.append(self._block(_ref(m), _ref(other), (m.start(), other.end())))
        if not out:
            m = re.search(rf"\b(?:blocks|blocking)\s+", clause)
            if m:
                head = clause[:m.start()]
                other = REF.match(clause, m.end())
                last = ctx["last"]
                if other and not REF.search(head) and last is not None and last.source:
                    out.append(self._block(_ref(other), {"color": last.color, "piece": last.piece, "square": last.target},
                                           (m.start(), other.end())))
        return out

    def _block(self, slider, blocker, span):
        return (Fact(predicate="blocks", args={"slider": slider["square"], "blocker": blocker["square"]}),
                _guards(slider) + _guards(blocker), None, span)

    def _files(self, clause, ctx, masked, cs) -> list:
        out = []
        for m in re.finditer(rf"(?<!\bto )\b(semi-open|open|opens|opening)\s+(?:the\s+)?(?:([a-h])-file|({SQ})-({SQ}))", clause):
            word, letter, a, b = m.groups()
            if a and a[0] != b[0]:
                fact = Fact(predicate="is_open_diagonal", args={"source": a, "target": b})
                if abs(ord(a[0]) - ord(b[0])) != abs(int(a[1]) - int(b[1])):
                    continue
            else:
                letter = letter or a[0]
                fact = Fact(predicate="is_open_file", args={"file": letter})
            if word == "semi-open":
                last = ctx["last"]
                if last is None or fact.predicate != "is_open_file":
                    continue
                fact = Fact(predicate="is_semi_open_file", args={"file": letter, "color": last.color})
            created = word in ("opens", "opening")
            if created and ctx["last"] is None and ctx["line"].prefix():
                continue
            out.append((fact, (), fact if created and ctx["last"] is not None else None, m.span()))
        for m in re.finditer(r"\bthe\s+([a-h])-file\s+is\s+open\b", clause, re.I):
            out.append((Fact(predicate="is_open_file", args={"file": m.group(1)}), (), None, m.span()))
        return out

    def _passed(self, clause, ctx, masked, cs) -> list:
        out = []
        created = re.search(rf"\bcreat(?:es|ing)\s+a\s+(?:second\s+)?passed\s+(?:(white|black)\s+)?(?:pawn(?:\s+on\s+({SQ}))?|([a-h])-pawn)\b", clause)
        last = ctx["last"]
        if created:
            color, square, letter = created.groups()
            if last is None or last.piece != "pawn" or not last.source:
                return out
            target = square or last.target
            if (letter and letter != last.target[0]) or (square and square != last.target):
                return out
            guards = _guards({"square": target, "piece": "pawn", "color": color or last.color})
            return [(Fact(predicate="is_passed_pawn", args={"square": target}), guards,
                     Fact(predicate="is_passed_pawn", args={"square": last.source}), created.span())]
        for m in re.finditer(rf"\bpassed\s+(?:(white|black)\s+)?pawn\s+on\s+({SQ})\b", clause):
            out.append((Fact(predicate="is_passed_pawn", args={"square": m.group(2)}),
                        _guards({"square": m.group(2), "piece": "pawn", "color": m.group(1)}), None, m.span()))
        for m in REF.finditer(clause):
            if m.group(3) == "pawn" and re.match(r"\s+is\s+(?:a\s+)?(?:\w+\s+)?(?:passed|passer)\b", clause[m.end():]):
                out.append((Fact(predicate="is_passed_pawn", args={"square": m.group(4)}), _guards(_ref(m)), None,
                            (m.start(), len(clause))))
        return out

    def _promotion(self, clause, ctx, masked, cs) -> list:
        m = re.search(r"\b(one|two|three|four|[1-4])\s+(?:step|square|tempo|move)s?\s+from\s+promot\w+", clause)
        if not m:
            return []
        square = None
        ref = next((r for r in REF.finditer(clause) if r.group(3) == "pawn"), None)
        if ref:
            square = ref.group(4)
        elif ctx["subject"] and ctx["subject"]["piece"] == "pawn":
            square = ctx["subject"]["square"]
        elif ctx["last"] is not None and ctx["last"].piece == "pawn" and ctx["last"].target:
            square = ctx["last"].target
        else:
            square = self._recent_pawn(masked, cs)
        if square is None:
            return []
        return [(Fact(predicate="promotion_distance_is", args={"square": square, "moves": WORDS[m.group(1)]}),
                 _guards({"square": square, "piece": "pawn"}), None, m.span())]

    def _recent_pawn(self, masked, cs) -> Optional[str]:
        """The pawn just talked about: the last pawn named in the text so far, if only one
        square has been named for a pawn in the current paragraph."""
        para_start = masked.rfind("\n\n", 0, cs) + 1
        squares = [m.group(4) for m in REF.finditer(masked[para_start:cs]) if m.group(3) == "pawn"]
        squares += re.findall(rf"\bpawn\s+on\s+({SQ})", masked[para_start:cs])
        return squares[-1] if squares and len(set(squares)) == 1 else None

    def _state(self, clause, ctx, masked, cs) -> list:
        out = []
        if re.search(r"\bis\s+checkmate\b", clause) or re.fullmatch(r"\s*checkmate\s*\.?\s*", clause):
            out.append((Fact(predicate="is_checkmate", args={}), (), None, (0, len(clause))))
        if re.search(r"\bdelivers\s+check\b", clause):
            out.append((Fact(predicate="is_check", args={}), (), None, (0, len(clause))))
        if re.search(r"\bno\s+legal\s+(?:reply|replies|moves?)\b", clause):
            out.append((Fact(predicate="legal_move_count_is", args={"count": 0}), (), None, (0, len(clause))))
        if re.search(r"\bmaterial\s+equality\b|\bmaterial\s+is\s+equal\b|\bequal\s+material\b", clause, re.I):
            out.append((Fact(predicate="material_balance_is", args={"points": 0}), (), None, (0, len(clause))))
        return out

    def _placement(self, clause, ctx, masked, cs) -> list:
        out = []
        for m in re.finditer(rf"\b(?:(white|black)\s+|(?:the\s+)?(?:\w+'s\s+)?(?:lone\s+)?)({PIECE})\s+(?:sits|stands|still)\s+on\s+({SQ})\b", clause):
            args = {"square": m.group(3), "piece": m.group(2)}
            if m.group(1):
                args["color"] = m.group(1)
            out.append((Fact(predicate="piece_at", args=args), (), None, m.span()))
        before = masked[max(0, cs - 6):cs]
        square = re.search(rf"({SQ})\s*\($", before)
        occupied = re.match(rf"\s*occupied by the (white|black) ({PIECE})\s*$", clause)
        if square and occupied:
            out.append((Fact(predicate="piece_at", args={"square": square.group(1), "color": occupied.group(1),
                                                         "piece": occupied.group(2)}), (), None, (0, len(clause))))
        return out

    def _result(self) -> Extraction:
        return Extraction(source_id=self.source_id, root_fen=self.root_fen, facts=tuple(self.facts),
                          variations=tuple(self.variations))


def extract(text: str, root_fen: str, source_id: str = "") -> Extraction:
    """Candidates for one model output. Never raises on prose; it reports PARSE_ERROR."""
    return _Extractor(text, root_fen, source_id).run()


def extract_variation(text: str, root_fen: str, source_id: str = "") -> ExtractedVariation:
    """A line given on its own: SAN ("Bxf7+ Kxf7 Nd5+") or QUEEN's verbose notation. Anything
    left unread, or ambiguous, is reported rather than guessed around."""
    def bad(status, reason):
        return ExtractedVariation(source_id=source_id, sentence=text, span_start=0, span_end=len(text), status=status,
                                  kind="text", reason=reason)
    if not text.strip():
        return bad("PARSE_ERROR", "empty text")
    board = chess.Board(root_fen)
    mentions, pos = [], 0
    pattern = re.compile(rf"\s*(?:\d+\s*(?:\.\.\.|…|\.)\s*)?(?:{_VERBOSE}|({_SAN_TOKEN})(?![\w-]))")
    while pos < len(text.rstrip()):
        m = pattern.match(text, pos)
        if m is None:
            return bad("PARSE_ERROR", f"unreadable at '{text[pos:pos + 20].strip()}'")
        g = m.groups()
        if g[-1]:
            mentions.append(_Mention(m.start(), m.end(), None, None, None, None, None, None, None, None, None, g[-1]))
        else:
            mentions.append(_Mention(m.start(), m.end(), None, None, g[0], g[1], g[2], g[3] or g[5], g[4], g[6], g[7]))
        pos = m.end()
    ucis = []
    for m in mentions:
        try:
            mv = board.parse_san(m.text_san())
        except chess.AmbiguousMoveError:
            return bad("AMBIGUOUS", f"'{m.text_san()}' fits more than one piece")
        except ValueError:
            ucis.append(m.uci())   # illegal is the verifier's call; the rest of the line cannot be placed
            break
        ucis.append(mv.uci())
        board.push(mv)
    return ExtractedVariation(source_id=source_id, sentence=text, span_start=0, span_end=len(text), status="EXTRACTED",
                              kind="text", plies=tuple(m.ply() for m in mentions), ucis=tuple(ucis))

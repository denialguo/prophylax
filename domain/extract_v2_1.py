"""Extractor V2.1: a conservative hybrid. V1's grammar, V2's recorded LLM proposals and a small set of
deterministic phrase rules each propose facts, and a deterministic merger reconciles them. Variations
stay V1's. Not used in production.

No source outranks another by name. Duplicates merge and keep their provenance. Competing readings of
one statement are settled only by what the text itself writes or by two sources agreeing, and
otherwise become AMBIGUOUS, which the verifier never judges. Provenance is for debugging and never
reaches a verdict. Truth stays with domain/verify.py."""
import difflib
import re
import unicodedata
from typing import Optional

import chess

from domain.extract import _san_mentions, _verbose_mentions, extract
from domain.extract_v2 import (_STATE_PREDICATES, ExtractedFactV2, SemanticExtractorBackend, _Model, _playable, _Reader, _SENTENCE_END, extract_v2, split_chunks,
                               state_before)
from domain.extract_v2 import ExtractedVariation
from domain.verify import Fact

VERSION = "v2.1-1"
FUZZY_THRESHOLD = 0.92
FUZZY_MARGIN = 0.04
SQ = r"[a-h][1-8]"
PIECE = r"pawn|knight|bishop|rook|queen|king"
SIDE = r"White|Black"
NUMBERS = {"no": 0, "zero": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "single": 1, "lone": 1}
_NUMBER = "|".join(sorted(NUMBERS, key=len, reverse=True))
VALUES = {"pawn": 1, "knight": 3, "bishop": 3, "rook": 5, "queen": 9, "exchange": 2}
_OPTIONAL = ("piece", "color")
_CHANGE_ALSO = re.compile(r"\b(becom\w+|became)\b", re.I)
_NEGATION = re.compile(r"\b(?:not(?! only)|no longer|never|cannot|can't|isn't|doesn't|does not|fails? to|without)\b", re.I)
_NO_LONGER = re.compile(r"\bno longer\b", re.I)
_SQUARE = re.compile(rf"\b{SQ}\b")


class HybridFact(ExtractedFactV2):
    produced_by: tuple[str, ...] = ()              # any of v1, v2, rules
    candidate_ids: tuple[str, ...] = ()
    normalized: bool = False                       # a deterministic rule wrote or changed this claim
    alignment: Optional[dict] = None               # how a V2 quote was found in QUEEN's text
    conflict: Optional[str] = None                 # how a competing reading was settled
    competing: tuple[str, ...] = ()
    held_before: Optional[Fact] = None             # "no longer": this must have held before the last move


class ExtractionV21(_Model):
    source_id: str
    root_fen: str
    facts: tuple[HybridFact, ...] = ()
    variations: tuple[ExtractedVariation, ...] = ()
    backend: dict = {}


# ── quote alignment ──────────────────────────────────────────────────────────
_FOLD = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "―": "-", "−": "-", "‐": "-", "‑": "-", "…": "..."}


def _folded(text: str) -> tuple:
    """Lower-cased text with spacing, dashes and quotes evened out, plus each character's source index."""
    out, index = [], []
    for i, ch in enumerate(text):
        for c in _FOLD.get(ch, unicodedata.normalize("NFKC", ch)):
            c = c.lower()
            if c.isspace():
                if out and out[-1] != " ":
                    out.append(" ")
                    index.append(i)
                continue
            out.append(c)
            index.append(i)
    return "".join(out), index


def _strip_edges(folded: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", folded.strip(" .,;:!?\"'-()"))


def _all_at(hay: str, needle: str) -> list:
    return [m.start() for m in re.finditer(re.escape(needle), hay)] if needle else []


def align_quote(quote: str, text: str, start: int, end: int, used: Optional[dict] = None) -> dict:
    """Finds a model's quote in one chunk, and never anywhere else. Returns method (exact, normalized,
    fuzzy, ambiguous, invented), the source span, and a score. Exact words repeated in the chunk are
    taken in order. Anything looser has to be unique or it is not trusted."""
    used = {} if used is None else used
    result = {"model_quote": quote, "method": "invented", "span": None, "score": None}
    if not quote or not quote.strip():
        return result
    pos = text.find(quote, used.get(quote, start), end)
    if pos < 0:
        pos = text.find(quote, start, end)
    if pos >= 0:
        used[quote] = pos + len(quote)
        return {**result, "method": "exact", "span": (pos, pos + len(quote)), "score": 1.0}
    chunk = text[start:end]
    folded, index = _folded(chunk)
    for needle in (_folded(quote)[0].strip(), _strip_edges(_folded(quote)[0])):
        hits = _all_at(folded, needle)
        if len(hits) == 1:
            s, e = index[hits[0]], index[hits[0] + len(needle) - 1] + 1
            return {**result, "method": "normalized", "span": (start + s, start + e), "score": 1.0}
        if len(hits) > 1:
            return {**result, "method": "ambiguous"}
    return _fuzzy(quote, folded, index, start, result)


def _fuzzy(quote, folded, index, start, result):
    """Same number of words, nearly the same letters, and no close second place. Long enough to mean
    something: a short quote that nearly matches is a different quote."""
    needle = _folded(quote)[0].strip()
    words = needle.split(" ")
    if len(words) < 3:
        return result
    spans = [m.span() for m in re.finditer(r"\S+", folded)]
    scored = []
    for size in (len(words) - 1, len(words), len(words) + 1):
        for i in range(max(0, len(spans) - size + 1)):
            s, e = spans[i][0], spans[i + size - 1][1]
            scored.append((difflib.SequenceMatcher(None, needle, folded[s:e], autojunk=False).ratio(), s, e))
    if not scored:
        return result
    best = max(scored)
    if best[0] < FUZZY_THRESHOLD:
        return result
    rivals = [r for r in scored if r[0] >= best[0] - FUZZY_MARGIN and (r[2] <= best[1] or r[1] >= best[2])]  # elsewhere in the chunk
    if rivals:
        return {**result, "method": "ambiguous", "score": round(best[0], 3)}
    return {**result, "method": "fuzzy", "span": (start + index[best[1]], start + index[best[2] - 1] + 1), "score": round(best[0], 3)}


# ── V2 proposals, read with alignment and stricter checks ────────────────────
class _HybridReader(_Reader):
    def __init__(self, *args):
        super().__init__(*args)
        self.last = None

    def _locate(self, quote):
        self.last = align_quote(quote, self.text, self.start, self.end, self.used)
        return self.last["span"]

    def claim(self, raw):
        self.last = None
        fact = super().claim(raw)
        if fact is None:
            return None
        alignment = self.last
        extra = {"produced_by": ("v2",), "alignment": {k: v for k, v in alignment.items() if k != "span"} if alignment else None}
        data = fact.model_dump(exclude={"fact", "guards", "change_from"})
        hybrid = HybridFact(**data, fact=fact.fact, guards=fact.guards, change_from=fact.change_from, **extra)
        if alignment and alignment["method"] == "ambiguous":
            return hybrid.model_copy(update={"status": "AMBIGUOUS", "reason": "the quote fits more than one place in the text", "span_start": self.start, "span_end": self.end})
        if alignment and alignment["span"]:
            hybrid = hybrid.model_copy(update={"sentence": self.text[alignment["span"][0]:alignment["span"][1]]})
        if hybrid.status != "EXTRACTED":
            return hybrid
        return self._stricter(hybrid)

    def _stricter(self, c: HybridFact) -> HybridFact:
        def ambiguous(reason):
            return c.model_copy(update={"status": "AMBIGUOUS", "reason": reason, "fact": None, "guards": (), "change_from": None})
        quote = self.text[c.span_start:c.span_end]
        if c.branch_source == "model":
            return ambiguous("the branch is not in the deterministic catalogue")
        if c.polarity == "positive" and _NEGATION.search(quote):
            return ambiguous("the quote contains a negation but the claim is positive")
        if c.fact.predicate == "piece_at" and c.fact.args["square"] not in _SQUARE.findall(quote):
            return ambiguous("the quote does not name the square this piece_at claims")
        if c.polarity == "negative" and _NO_LONGER.search(self.text[max(self.start, c.span_start - 25):c.span_end]):
            return c.model_copy(update={"held_before": c.fact})
        if (c.polarity == "positive" and c.change_from is None and c.fact.predicate in _STATE_PREDICATES
                and _CHANGE_ALSO.search(self.text[max(self.start, c.span_start - 30):c.span_end])):
            return c.model_copy(update={"change_from": state_before(c.fact, list(c.prefix) if c.prefix is not None else None)})
        return c


# ── where in the prose a rule's claim stands ─────────────────────────────────
def _mentions(text: str) -> list:
    verbose = _verbose_mentions(text)
    return sorted(verbose + _san_mentions(text, verbose), key=lambda m: m.start)


def _paragraph_start(text: str, pos: int) -> int:
    found = text.rfind("\n\n", 0, pos)
    return found + 2 if found >= 0 else 0


def board_at(text: str, fen: str, v1, pos: int):
    """The branch the prose has reached at pos: the moves of the line around it, up to the last move
    written before pos. Root when nothing has been played yet. None when that cannot be told."""
    start = _paragraph_start(text, pos)
    lines = [v for v in v1.variations if v.status == "EXTRACTED" and v.kind == "prose" and v.span_start <= pos and start <= v.span_end]
    if not lines:
        return [] if not v1_moves_before(text, pos) else None
    line = max(lines, key=lambda v: v.span_start)
    mentions = _mentions(text[line.span_start:line.span_end])
    if not mentions:
        return None
    reached = sum(line.span_start + m.end <= pos for m in mentions)
    offset = len(line.ucis) - len(mentions)
    if offset < 0:
        return None
    branch = list(line.ucis[:offset + reached])
    return branch if len(_playable(fen, branch)) == len(branch) else None


def v1_moves_before(text: str, pos: int) -> bool:
    return bool(_mentions(text[:pos]))


def _unique_piece(fen, branch, piece, color=None):
    """The one piece of this kind on the board, or None. Used to resolve "the rook", never to judge a claim."""
    if branch is None:
        return None
    board = chess.Board(fen)
    for uci in branch:
        board.push_uci(uci)
    kind = chess.PIECE_NAMES.index(piece)
    found = [s for c in ((color == "white",) if color else (True, False)) for s in board.pieces(kind, c)]
    return chess.square_name(found[0]) if len(found) == 1 else None


# ── deterministic phrase rules ───────────────────────────────────────────────
_ITEM = rf"(?:(?:a|an|one|two|three|four|five|six|seven|eight|no|lone|single)\s+)+(?:{PIECE})s?"
_LIST = rf"{_ITEM}(?:(?:,\s+and\s+|,\s+|\s+and\s+){_ITEM})*"
_HAS = re.compile(rf"\b({SIDE})\s+(?:(does not have|doesn't have)|has|retains|keeps|is left with|remains with)\s+(only\s+|just\s+|exactly\s+)?({_LIST})(?:\s+against\s+({_LIST}))?(?!\s+(?:on|at|to|in)\b)(?![\w-])")
_BARE_ITEM = rf"(?:(?:a|an|one|two|three|four|five|six|seven|eight|no|lone|single)\s+)*(?:{PIECE})s?"
_BARE_LIST = rf"{_BARE_ITEM}(?:(?:,\s+and\s+|,\s+|\s+and\s+){_BARE_ITEM})*"
_AGAINST = re.compile(rf"\b({SIDE})\b[^.\n]{{0,70}}?[—:]\s*({_BARE_LIST})\s+against\s+({_BARE_LIST})(?![\w-])")
_NO_PIECES = re.compile(rf"\b({SIDE})\s+has\s+no\s+pieces\b")
_THERE_IS_NO = re.compile(rf"\bthere\s+is\s+no\s+(?:(white|black)\s+)?({PIECE})\b", re.I)
_REMAINS = re.compile(rf"\bonly\s+(one|two|three)\s+(?:(white|black)\s+)?({PIECE})s?\s+remains?\b", re.I)
_DIRECTION = {"up": 1, "ahead": 1, "down": -1, "behind": -1}
_AMOUNT = rf"(?:(?:a|an|the|one|two|three|a\s+full|a\s+whole|by\s+a|by\s+an)\s+)?"
_UNIT = rf"(?:{PIECE}|exchange)s?"
_MATERIAL = re.compile(rf"\b({SIDE})\s+(?:is|remains|stands|stays)(\s+not)?(?:\s+now|\s+still)?\s+(?:(up|down|ahead|behind)\s+({_AMOUNT})({_UNIT})|({_AMOUNT})({_UNIT})\s+(up|down|ahead|behind))\b")
_LEAVES = re.compile(rf"\bleav(?:es|ing)\s+({SIDE})\s+({_AMOUNT})({_UNIT})\s+(up|down|ahead|behind)\b")
_SIDELESS = re.compile(rf"\b({_AMOUNT})({_UNIT})\s+(up|down|ahead|behind)\b")
_EQUAL = re.compile(r"\bmaterial\s+(?:is|remains|stays)(\s+not)?\s+(?:equal|level|even|balanced)\b|\b(?:equal\s+material|material\s+(?:equality|parity))\b", re.I)
_PIECE_REF = rf"(?:[Tt]he\s+)?(?:(white|black)\s+)?({PIECE})\s+on\s+({SQ})"
_SQ_PAWN = rf"(?:[Tt]he\s+)?({SQ})[\s-]pawn"
_FILE = r"(?:[Tt]he\s+)?(?:([a-h])-file|([a-h])1-\2?[a-h]?8)"
_NOT_PASSED = re.compile(rf"(?:{_PIECE_REF}|{_SQ_PAWN})\s+(?:is\s+not|isn't|is\s+no\s+longer)\s+(?:a\s+)?passed(?:\s+pawn)?\b")
_BARE_PASSED = re.compile(r"\b[Tt]he\s+pawn\s+(?:is\s+not|isn't|is\s+no\s+longer)\s+(?:a\s+)?passed(?:\s+pawn)?\b")
_NOT_OPEN = re.compile(r"\b[Tt]he\s+([a-h])-file\s+(?:is\s+not|isn't|is\s+no\s+longer)\s+open\b")
_NEG_VERB = rf"(no\s+longer|does\s+not|doesn't)\s+(attacks?|defends?|protects?)"
_TARGET = rf"(?:(?:[Tt]he\s+)?(?:(white|black)\s+)?({PIECE})\s+on\s+)?({SQ})"
_NEG_REL = re.compile(rf"{_PIECE_REF}\s+{_NEG_VERB}\s+{_TARGET}\b")
_NEG_REL_BARE = re.compile(rf"\b[Tt]he\s+(?:(white|black)\s+)?({PIECE})\s+{_NEG_VERB}\s+{_TARGET}\b")
_POS_REL = re.compile(rf"{_PIECE_REF}\s+(?:now\s+|also\s+|directly\s+|immediately\s+)?(attacks|is\s+attacking|threatens|is\s+threatening|hits|defends|protects)\s+{_TARGET}\b")
_PARTICIPLE = re.compile(rf",\s+(?:also\s+|directly\s+|immediately\s+|simultaneously\s+)?(attacking|threatening|hitting)\s+(?:[Tt]he\s+)?(?:(white|black)\s+)?({PIECE})\s+on\s+({SQ})(?:\s+and\s+(?:[Tt]he\s+)?(?:(white|black)\s+)?({PIECE})\s+on\s+({SQ}))?")


class _Rules:
    """Phrases that map cleanly onto a predicate. Anything without a clear side or referent is reported
    AMBIGUOUS, and nothing is read from language these patterns do not name."""

    def __init__(self, text, fen, v1, source_id):
        self.text, self.fen, self.v1, self.source_id = text, fen, v1, source_id
        self.out, self.n = [], 0

    def add(self, m, predicate=None, args=None, polarity="positive", status="EXTRACTED", reason=None, guards=(), held=False, group=0):
        start, end = m.span(group)
        branch = board_at(self.text, self.fen, self.v1, start) if status == "EXTRACTED" else None
        self.n += 1
        fact = Fact(predicate=predicate, args=args) if status == "EXTRACTED" else None
        self.out.append(HybridFact(source_id=self.source_id, sentence=self.text[start:end], span_start=start, span_end=end, status=status, reason=reason, polarity=polarity, fact=fact,
                                   guards=tuple(guards), prefix=tuple(branch) if branch is not None else None, produced_by=("rules",), candidate_ids=(f"rules:{self.n}",), normalized=True,
                                   held_before=fact if held else None))

    @staticmethod
    def _items(listing):
        """(count, piece) for each item of "a rook and six pawns"."""
        out = []
        for m in re.finditer(_BARE_ITEM, listing):
            words = m.group(0).split()
            number = next((NUMBERS[w] for w in words[:-1] if w in NUMBERS and w != "lone"), 1)
            out.append((number, words[-1].rstrip("s") if words[-1] != "bishops" else "bishop"))
        return out

    def counts(self):
        for m in _HAS.finditer(self.text):
            side, denied, _, listing, other = m.groups()
            color = side.lower()
            for number, piece in self._items(listing):
                if denied and number == 1:
                    self.add(m, "piece_count_is", {"color": color, "piece": piece, "count": 0})  # "does not have a queen" says count 0 directly
                else:
                    self.add(m, "piece_count_is", {"color": color, "piece": piece, "count": number}, polarity="negative" if denied else "positive")
            for number, piece in self._items(other or ""):
                self.add(m, "piece_count_is", {"color": "black" if color == "white" else "white", "piece": piece, "count": number})
        for m in _AGAINST.finditer(self.text):
            color = m.group(1).lower()
            for number, piece in self._items(m.group(2)):
                self.add(m, "piece_count_is", {"color": color, "piece": piece, "count": number}, group=2)
            for number, piece in self._items(m.group(3)):
                self.add(m, "piece_count_is", {"color": "black" if color == "white" else "white", "piece": piece, "count": number}, group=3)
        for m in _NO_PIECES.finditer(self.text):
            for piece in ("knight", "bishop", "rook", "queen"):
                self.add(m, "piece_count_is", {"color": m.group(1).lower(), "piece": piece, "count": 0})
        for m in _THERE_IS_NO.finditer(self.text):
            if m.group(1):
                self.add(m, "piece_count_is", {"color": m.group(1).lower(), "piece": m.group(2).lower(), "count": 0})
            else:
                self.add(m, status="AMBIGUOUS", reason="no side is named")
        for m in _REMAINS.finditer(self.text):
            if m.group(2):
                self.add(m, "piece_count_is", {"color": m.group(2).lower(), "piece": m.group(3).lower(), "count": NUMBERS[m.group(1).lower()]})
            else:
                self.add(m, status="AMBIGUOUS", reason="no side is named")

    @staticmethod
    def _points(amount, unit, direction, side):
        number = next((n for w, n in (("two", 2), ("three", 3)) if w in (amount or "")), 1)
        points = number * VALUES[unit.rstrip("s")] * _DIRECTION[direction]
        return points if side == "White" else -points

    def material(self):
        taken = []
        for m in _MATERIAL.finditer(self.text):
            side, negated, d1, a1, u1, a2, u2, d2 = m.groups()
            self.add(m, "material_balance_is", {"points": self._points(a1 or a2, u1 or u2, d1 or d2, side)}, polarity="negative" if negated else "positive")
            taken.append(m.span())
        for m in _LEAVES.finditer(self.text):
            side, amount, unit, direction = m.groups()
            self.add(m, "material_balance_is", {"points": self._points(amount, unit, direction, side)})
            taken.append(m.span())
        for m in _SIDELESS.finditer(self.text):
            if not any(s <= m.start() and m.end() <= e for s, e in taken):
                self.add(m, status="AMBIGUOUS", reason="whose material this describes is not stated")
        for m in _EQUAL.finditer(self.text):
            self.add(m, "material_balance_is", {"points": 0}, polarity="negative" if m.group(1) else "positive")

    def negatives(self):
        for m in _NOT_PASSED.finditer(self.text):
            square = m.group(3) or m.group(4)
            self.add(m, "is_passed_pawn", {"square": square}, polarity="negative", held="no longer" in m.group(0))
        for m in _BARE_PASSED.finditer(self.text):
            self.add(m, status="AMBIGUOUS", reason="which pawn is not stated")
        for m in _NOT_OPEN.finditer(self.text):
            self.add(m, "is_open_file", {"file": m.group(1)}, polarity="negative", held="no longer" in m.group(0))
        for m in _NEG_REL.finditer(self.text):
            color, piece, square, how, verb, t_color, t_piece, target = m.groups()
            self._relation(m, verb, square, target, piece, t_color, t_piece, negative=True, held=how.startswith("no"), source_color=color)
        for m in _NEG_REL_BARE.finditer(self.text):
            color, piece, how, verb, t_color, t_piece, target = m.groups()
            branch = board_at(self.text, self.fen, self.v1, m.start())
            square = _unique_piece(self.fen, branch, piece, color)
            if square is None:
                self.add(m, status="AMBIGUOUS", reason=f"which {piece} is not settled")
            else:
                self._relation(m, verb, square, target, piece, t_color, t_piece, negative=True, held=how.startswith("no"), source_color=color)

    def _relation(self, m, verb, source, target, piece, t_color, t_piece, negative=False, held=False, source_color=None, group=0):
        predicate = "defends" if verb.startswith(("defend", "protect")) else "attacks"
        guards = []
        if t_piece:
            guards.append(Fact(predicate="piece_at", args={k: v for k, v in (("square", target), ("color", t_color), ("piece", t_piece)) if v}))
        if source_color:
            guards.append(Fact(predicate="piece_at", args={"square": source, "color": source_color, "piece": piece}))
        self.add(m, predicate, {"source": source, "target": target, "piece": piece}, polarity="negative" if negative else "positive", guards=guards, held=held, group=group)

    def attacks(self):
        for m in _POS_REL.finditer(self.text):
            color, piece, square, verb, t_color, t_piece, target = m.groups()
            if verb.lower().endswith(("threatens", "threatening")) and not t_piece:
                continue  # "threatens g5" may mean a move: only a named piece makes it an attack
            self._relation(m, verb.split()[-1], square, target, piece, t_color, t_piece, source_color=color)
        mentions = _mentions(self.text)
        for m in _PARTICIPLE.finditer(self.text):
            mover = next((x for x in reversed(mentions) if x.end <= m.start()), None)
            if mover is None or mover.target is None or m.start() - mover.end > 60 or _SENTENCE_END.search(self.text, mover.end, m.start()):
                continue  # the participle has to hang off the move just written
            verb, c1, p1, t1, c2, p2, t2 = m.groups()
            self._relation(m, verb, mover.target, t1, mover.piece, c1, p1, source_color=mover.color)
            if t2:
                self._relation(m, verb, mover.target, t2, mover.piece, c2, p2, source_color=mover.color)

    def run(self):
        self.counts()
        self.material()
        self.negatives()
        self.attacks()
        return self.out


def rule_claims(text: str, fen: str, v1, source_id: str = "") -> list:
    """Only prose: the answer block belongs to the move parser."""
    chunks = split_chunks(text)
    limit = chunks[-1][1] if chunks else 0
    return [c for c in _Rules(text, fen, v1, source_id).run() if c.span_end <= limit]


# ── the merger ───────────────────────────────────────────────────────────────
def _required(fact):
    return {k: v for k, v in fact.args.items() if k not in _OPTIONAL}


def same_claim(a: HybridFact, b: HybridFact) -> bool:
    """Same statement about the same board: spans overlap, and predicate, arguments, polarity and
    branch agree. A side that leaves out an optional piece or colour is not disagreeing."""
    if a.polarity != b.polarity or a.prefix != b.prefix or a.fact.predicate != b.fact.predicate or _required(a.fact) != _required(b.fact):
        return False
    if not (a.span_start < b.span_end and b.span_start < a.span_end):
        return False
    return all(a.fact.args[k] == b.fact.args[k] for k in _OPTIONAL if k in a.fact.args and k in b.fact.args)


def _combined(group: list) -> HybridFact:
    """One claim from its duplicates: the narrowest span, every stated identity, and the stricter
    requirement wherever one source has it, so merging can never make a claim easier to verify."""
    base = min(group, key=lambda c: c.span_end - c.span_start)
    args = {}
    for c in group:
        args.update(c.fact.args)
    guards, seen = [], set()
    for c in group:
        for g in c.guards:
            key = str(sorted(g.args.items()))
            if key not in seen:
                seen.add(key)
                guards.append(g)
    return base.model_copy(update={"fact": Fact(predicate=base.fact.predicate, args=args), "guards": tuple(guards),
                                   "change_from": next((c.change_from for c in group if c.change_from is not None), None),
                                   "held_before": next((c.held_before for c in group if c.held_before is not None), None),
                                   "produced_by": tuple(sorted({s for c in group for s in c.produced_by})),
                                   "candidate_ids": tuple(i for c in group for i in c.candidate_ids),
                                   "normalized": any(c.normalized for c in group), "alignment": next((c.alignment for c in group if c.alignment), None),
                                   "branch_source": next((c.branch_source for c in group if c.branch_source), None)})


def _strong_overlap(a, b) -> bool:
    shared = min(a.span_end, b.span_end) - max(a.span_start, b.span_start)
    return shared > 0 and shared * 2 >= min(a.span_end - a.span_start, b.span_end - b.span_start)


def conflict_kind(a: HybridFact, b: HybridFact) -> Optional[str]:
    """Why two claims from separate sources cannot both be the reading of one statement, or None.
    Two different targets of one attacker are two claims, not a conflict."""
    if a.polarity != b.polarity or not _strong_overlap(a, b) or a.fact.predicate != b.fact.predicate:
        return None
    x, y, predicate = a.fact.args, b.fact.args, a.fact.predicate
    if _required(a.fact) == _required(b.fact):
        if a.prefix != b.prefix and a.prefix is not None and b.prefix is not None:
            return "board"
        if any(x[k] != y[k] for k in _OPTIONAL if k in x and k in y):
            return "identity"
        return None
    if a.prefix != b.prefix:
        return None
    if predicate in ("attacks", "defends") and x["target"] == y["target"] and x["source"] != y["source"]:
        return "source"
    if predicate in ("is_passed_pawn", "is_open_file", "is_semi_open_file", "has_passed_pawn") and _required(a.fact) != _required(b.fact):
        return "referent"
    return None


def _describe(c: HybridFact) -> str:
    board = "no board" if c.prefix is None else (" ".join(c.prefix) or "root")
    return f"{c.fact.predicate}({c.fact.args}) on {board}, from {'+'.join(c.produced_by)}"


def _written_squares(c: HybridFact, text: str) -> bool:
    quote = _SQUARE.findall(text[c.span_start:c.span_end])
    squares = [v for k, v in c.fact.args.items() if k in ("square", "source", "target", "slider", "blocker", "pinner", "pinned")]
    return bool(squares) and all(s in quote for s in squares)


def _prose_settles(text, fen, v1, a, b):
    """True if a's board is the one the prose has reached at this claim, False if b's, None if neither or no way to tell."""
    if v1 is None:
        return None
    reached = board_at(text, fen, v1, max(a.span_end, b.span_end))
    if reached is None:
        return None
    reached = tuple(reached)
    if (a.prefix == reached) == (b.prefix == reached):
        return None
    return a.prefix == reached


def merge(text: str, candidates: list, fen: str = None, v1=None) -> list:
    """Deterministic. Duplicates become one claim. Then, among claims from separate sources: agreement
    settles a conflict, then what the text writes (squares in the quote, or for a board, the moves
    written before the claim), and otherwise both become AMBIGUOUS."""
    extracted = [c for c in candidates if c.status == "EXTRACTED"]
    others = [c for c in candidates if c.status != "EXTRACTED"]
    groups = []
    for c in sorted(extracted, key=lambda c: (c.span_start, c.span_end, c.produced_by, str(c.fact.args))):
        for group in groups:
            if any(same_claim(c, g) for g in group):
                group.append(c)
                break
        else:
            groups.append([c])
    merged = [_combined(g) for g in groups]
    # a claim with no board gives way to the same claim on a board
    merged = [c for c in merged if not (c.prefix is None and any(o is not c and o.prefix is not None and o.polarity == c.polarity and o.fact.predicate == c.fact.predicate
                                                                 and _required(o.fact) == _required(c.fact) and _strong_overlap(o, c) for o in merged))]
    removed, added, updated = set(), [], {}
    for i, a in enumerate(merged):
        for j in range(i + 1, len(merged)):
            b = merged[j]
            if i in removed or j in removed or set(a.produced_by) & set(b.produced_by):
                continue
            kind = conflict_kind(a, b)
            if kind is None:
                continue
            a_agreed, b_agreed = len(a.produced_by) > 1, len(b.produced_by) > 1
            a_written, b_written = _written_squares(a, text), _written_squares(b, text)
            if a_agreed != b_agreed:
                keep, drop, how = (i, j, "agreement") if a_agreed else (j, i, "agreement")
            elif kind != "board" and a_written != b_written:
                keep, drop, how = (i, j, "written squares") if a_written else (j, i, "written squares")
            elif kind == "board" and _prose_settles(text, fen, v1, a, b) is not None:
                first = _prose_settles(text, fen, v1, a, b)
                keep, drop, how = (i, j, "the moves written before it") if first else (j, i, "the moves written before it")
            else:
                removed.update((i, j))
                added.append(HybridFact(source_id=a.source_id, sentence=text[min(a.span_start, b.span_start):max(a.span_end, b.span_end)], span_start=min(a.span_start, b.span_start),
                                        span_end=max(a.span_end, b.span_end), status="AMBIGUOUS", reason=f"sources disagree on the {kind}", polarity=a.polarity,
                                        interpretations=(_describe(a), _describe(b)), competing=(_describe(a), _describe(b)), conflict=f"unresolved {kind}",
                                        produced_by=tuple(sorted(set(a.produced_by) | set(b.produced_by))), candidate_ids=a.candidate_ids + b.candidate_ids))
                continue
            removed.add(drop)
            updated[keep] = merged[keep].model_copy(update={"conflict": f"{kind} settled by {how}", "competing": (_describe(merged[drop]),)})
    final = [updated.get(i, c) for i, c in enumerate(merged) if i not in removed] + added
    return sorted(final + others, key=lambda c: (c.span_start, c.span_end))


# ── the whole extractor ──────────────────────────────────────────────────────
def _from_v1(v1) -> list:
    out = []
    for n, c in enumerate(v1.facts):
        out.append(HybridFact(source_id=c.source_id, sentence=c.sentence, span_start=c.span_start, span_end=c.span_end, status=c.status, fact=c.fact, guards=c.guards, change_from=c.change_from,
                              prefix=c.prefix, reason=c.reason, produced_by=("v1",), candidate_ids=(f"v1:{n}",)))
    return out


def extract_hybrid(text: str, root_fen: str, backend: SemanticExtractorBackend, source_id: str = "") -> ExtractionV21:
    """Every source's claims, merged. V2's left-over sentences are dropped: V1 classifies every sentence itself."""
    v1 = extract(text, root_fen, source_id)
    v2 = extract_v2(text, root_fen, backend, source_id, reader=_HybridReader)
    proposals = []
    for n, c in enumerate(f for f in v2.facts if not f.residual):
        if isinstance(c, HybridFact):
            proposals.append(c.model_copy(update={"candidate_ids": (f"v2:{n}",)}))
        else:  # a rejection for the whole chunk
            proposals.append(HybridFact(**c.model_dump(exclude={"fact", "guards", "change_from"}), produced_by=("v2",), candidate_ids=(f"v2:{n}",)))
    facts = merge(text, _from_v1(v1) + proposals + rule_claims(text, root_fen, v1, source_id), root_fen, v1)
    return ExtractionV21(source_id=source_id, root_fen=root_fen, facts=tuple(facts), variations=v1.variations, backend={**v2.backend, "hybrid_version": VERSION})

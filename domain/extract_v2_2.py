"""Extractor V2.2: V2.1's hybrid and merger, with stricter meaning. Not used in production.

The merger is V2.1's, untouched. What changes is what a phrase is allowed to mean. A balance needs a
written figure: "a queen down" is a relative deficit, and White can be a queen down and up a rook and
pawn, so it maps to no predicate. Negation stops at its own clause."""
import re

from domain import extract_v2_1 as h
from domain.extract import extract
from domain.extract_v2 import SemanticExtractorBackend, extract_v2, split_chunks
from domain.extract_v2_1 import NUMBERS, ExtractionV21, HybridFact, board_at, merge

VERSION = "v2.2-1"
_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_N = r"\d{1,2}|" + "|".join(sorted(_WORDS, key=len, reverse=True))
_SIDE_OF = rf"({h.SIDE})(?:['’]s)?"
_BE = r"(?:is|remains|stands|stays)(?:\s+now|\s+still)?"
_POINTS_AFTER = re.compile(rf"\b{_SIDE_OF}\s+{_BE}\s+(?:a\s+)?({_N})\s+points?\s+(up|down|ahead|behind)\b")
_POINTS_BEFORE = re.compile(rf"\b{_SIDE_OF}\s+{_BE}\s+(up|down|ahead|behind)\s+(?:by\s+)?({_N})\s+points?\b")
_LEAD = {"surplus": 1, "advantage": 1, "edge": 1, "lead": 1, "deficit": -1}
_SURPLUS_WORDS = "|".join(_LEAD)
_SURPLUS = rf"(?:a\s+|an\s+)?({_N})[\s-]points?\s+material\s+({_SURPLUS_WORDS})\b"
_SIDED_SURPLUS = re.compile(rf"\b{_SIDE_OF}\s+(?:\w+\s+)?{_SURPLUS}")
_ANY_SURPLUS = re.compile(rf"\b{_SURPLUS}", re.I)
_STATED = re.compile(r"\bmaterial\s+balance\s+(?:is|of|stands\s+at|=)\s*([+\-−]\s*\d{1,2}|0)\b", re.I)
_ANY_FIGURE = re.compile(rf"\b({_N})[\s-]points?\b", re.I)
_LOSS = re.compile(r"\b(?:lost|loses|losing|sacrific\w+|gave\s+up|gives\s+up)\b", re.I)
_RELATIVE_REASON = "a relative deficit, not an exact balance or count"
_END = r"(?=\s*(?:$|[,.;:!?)\n—–-])|\s+(?:and|or|but|while|left|remaining|against|for|on\s+the\s+board)\b)"
_HAS = re.compile(rf"\b({h.SIDE})\s+(?:still\s+|now\s+)?(?:(does not have|doesn't have)|has|retains|keeps|is left with|remains with)\s+(only\s+|just\s+|exactly\s+)?({h._LIST})(?:\s+against\s+({h._LIST}))?{_END}")
_BOTH = re.compile(rf"\b({h.SIDE})\s+(?:still\s+|now\s+)?(?:has|retains|keeps)\s+both\s+(?:(?:of\s+)?(?:its|his|her|their)\s+)?(bishop|knight|rook)s{_END}")
_NOUN = rf"(?:a\s+|an\s+|any\s+)?({h.PIECE})s?{_END}"
_SIDE_WITHOUT = re.compile(rf"\b({h.SIDE})(?:,\s+|\s+(?:is|remains|plays|stands)\s+(?:on\s+)?)(?:without|with\s+no)\s+{_NOUN}")
_ANY_WITHOUT = re.compile(rf"\b(?:without|with\s+no)\s+{_NOUN}")
_SINGLE = re.compile(rf"\b(?:has|retains|keeps)\s+(?:a|an)\s+(?:{h.PIECE}){_END}(?!\s+(?:and|against)\b)")
_BOUNDARY = re.compile(r"[.;:!?\n,—–(]|\b(?:but|while|whereas|although|though|yet)\b", re.I)
_CLAUSE = re.compile(r"\s*;\s*|,?\s+(?:but|while|whereas|although|though|yet)\s+", re.I)
_SUBJECT = rf"(?:(?:[Tt]he\s+)|({h.SIDE})['’]s\s+)?(?:(white|black)\s+)?(?:({h.SQ})\s+)?\b({h.PIECE})(?:\s+on\s+({h.SQ}))?"
_NEG_FORM = r"does\s+not|doesn['’]t|is\s+no\s+longer|no\s+longer|is\s+not|isn['’]t"
_VERB = r"attacks?|attacking|defends?|defending|protects?|protecting"
_OBJECT = rf"(?:[Tt]he\s+)?(?:(white|black)\s+)?(?:(?:({h.PIECE})\s+on\s+)?({h.SQ})(?:[\s-]({h.PIECE}))?|({h.PIECE}))\b"
_NEGATED = re.compile(rf"\b{_SUBJECT}\s+({_NEG_FORM})\s+({_VERB})\s+{_OBJECT}")
_AFFIRMED = re.compile(rf"\b{_SUBJECT}\s+(?:now\s+|also\s+|directly\s+)?(attacks|defends|protects)\s+{_OBJECT}")
_THEN_DOES = re.compile(rf",?\s+(?:but|while|whereas)\s+({_SUBJECT}\s+does\b(?!\s+not|n['’]t))")
_THEN_DOES_NOT = re.compile(rf",?\s+(?:but|while|whereas)\s+({_SUBJECT}\s+(?:does\s+not|doesn['’]t)\b)")
_THEN_NOT = re.compile(rf",?\s+but\s+not\s+{_OBJECT}")
_NOT_PASSED = re.compile(rf"(?:{h._PIECE_REF}|(?:[Tt]he\s+)?({h.SQ})[\s-]pawn)\s+(?:is\s+not|isn['’]t|is\s+no\s+longer)\s+(?:a\s+)?(?:passed(?:\s+pawn)?|passer)\b")
_NOT_OPEN = re.compile(r"\b[Tt]he\s+([a-h])-file\s+(?:is\s+not|isn['’]t|is\s+no\s+longer)\s+open\b")
_NOT_PASSED_BARE = re.compile(r"\b[Tt]he\s+pawn\s+(?:is\s+not|isn['’]t|is\s+no\s+longer)\s+(?:a\s+)?(?:passed(?:\s+pawn)?|passer)\b")


def _number(word: str) -> int:
    return int(word) if word.isdigit() else _WORDS[word.lower()]


def _balance(side: str, amount: int) -> int:
    return amount if side == "White" else -amount


def _relative(quote: str) -> bool:
    return any(p.search(quote) for p in (h._MATERIAL, h._LEAVES, h._SIDELESS))


def _written_figure(quote: str):
    """The size of the balance the words state, or None. Equality is a figure: zero."""
    if h._EQUAL.search(quote):
        return 0
    stated = _STATED.search(quote)
    if stated:
        return abs(int(re.sub(r"\s", "", stated.group(1)).replace("−", "-")))
    figure = _ANY_FIGURE.search(quote)
    return _number(figure.group(1)) if figure else None


def _negated_before(text: str, start: int) -> bool:
    """A negation earlier in the clause this position stands in."""
    head = text[max(0, start - 160):start]
    edges = [m.end() for m in _BOUNDARY.finditer(head)]
    return bool(h._NEGATION.search(head[edges[-1] if edges else 0:]))


def checked(c: HybridFact, text: str) -> HybridFact:
    """Whichever source proposed it: a balance needs a written figure of that size, a count cannot be
    read from wording about a deficit, a loss or a bare "has a rook", and a positive claim cannot stand
    under a negation in its own clause. A piece_at mention is presupposed, so a denial does not reach it."""
    if c.status != "EXTRACTED":
        return c
    quote, predicate = text[c.span_start:c.span_end], c.fact.predicate

    def refuse(status, reason):
        return c.model_copy(update={"status": status, "reason": reason, "fact": None, "guards": (), "change_from": None, "held_before": None})
    if predicate == "material_balance_is":
        figure = _written_figure(quote)
        if figure is None:
            return refuse("UNSUPPORTED", _RELATIVE_REASON if _relative(quote) else "no figure is written for this balance")
        if figure != abs(int(c.fact.args["points"])):
            return refuse("AMBIGUOUS", f"the text writes {figure} points, the claim says {c.fact.args['points']}")
    elif predicate == "piece_count_is" and (_relative(quote) or _LOSS.search(quote)):
        return refuse("UNSUPPORTED", _RELATIVE_REASON)
    elif predicate == "piece_count_is" and _SINGLE.search(quote):
        return refuse("AMBIGUOUS", "this says there is one, not that there is exactly one")
    if c.polarity == "positive" and predicate != "piece_at" and _negated_before(text, c.span_start):
        return refuse("AMBIGUOUS", "a negation earlier in this clause may cover the claim")
    return c


def clause_of(quote: str, fact) -> tuple:
    """The span of the one clause a claim's subject stands in, or None when the quote has a single
    clause or the subject cannot be placed. Squares written in the text decide first, then the piece."""
    edges = [m.span() for m in _CLAUSE.finditer(quote)]
    if not edges:
        return None
    spans = [(edges[i - 1][1] if i else 0, edges[i][0] if i < len(edges) else len(quote)) for i in range(len(edges) + 1)]
    args = fact.args
    subject = next((args[k] for k in ("source", "slider", "pinner", "pinned", "square") if k in args), None)
    for word in (subject, args.get("piece")):
        hits = [s for s in spans if word and re.search(rf"\b{word}\b", quote[s[0]:s[1]], re.I)]
        if len(hits) == 1:
            return hits[0]
    return None


class _Rules(h._Rules):
    def counts(self):
        for m in _HAS.finditer(self.text):
            side, denied, only, listing, other = m.groups()
            color = side.lower()
            items = self._items(listing)
            if len(items) == 1 and not (denied or only or other) and re.match(r"(?:a|an)\s", listing):
                self.add(m, status="AMBIGUOUS", reason="this says there is one, not that there is exactly one")
                continue
            for number, piece in items:
                if denied and number == 1:
                    self.add(m, "piece_count_is", {"color": color, "piece": piece, "count": 0})
                else:
                    self.add(m, "piece_count_is", {"color": color, "piece": piece, "count": number}, polarity="negative" if denied else "positive")
            for number, piece in self._items(other or ""):
                self.add(m, "piece_count_is", {"color": "black" if color == "white" else "white", "piece": piece, "count": number})
        for m in h._AGAINST.finditer(self.text):
            color = m.group(1).lower()
            for number, piece in self._items(m.group(2)):
                self.add(m, "piece_count_is", {"color": color, "piece": piece, "count": number}, group=2)
            for number, piece in self._items(m.group(3)):
                self.add(m, "piece_count_is", {"color": "black" if color == "white" else "white", "piece": piece, "count": number}, group=3)
        for m in h._NO_PIECES.finditer(self.text):
            for piece in ("knight", "bishop", "rook", "queen"):
                self.add(m, "piece_count_is", {"color": m.group(1).lower(), "piece": piece, "count": 0})
        for m in _BOTH.finditer(self.text):
            self.add(m, "piece_count_is", {"color": m.group(1).lower(), "piece": m.group(2), "count": 2})
        taken = []
        for m in _SIDE_WITHOUT.finditer(self.text):
            self.add(m, "piece_count_is", {"color": m.group(1).lower(), "piece": m.group(2), "count": 0})
            taken.append(m.span())
        for m in _ANY_WITHOUT.finditer(self.text):
            if not any(s <= m.start() and m.end() <= e for s, e in taken):
                self.add(m, status="AMBIGUOUS", reason="no side is named")
        for pattern, piece_at, color_at, number_at in ((h._THERE_IS_NO, 2, 1, None), (h._REMAINS, 3, 2, 1)):
            for m in pattern.finditer(self.text):
                if m.group(color_at):
                    count = NUMBERS[m.group(number_at).lower()] if number_at else 0
                    self.add(m, "piece_count_is", {"color": m.group(color_at).lower(), "piece": m.group(piece_at).lower(), "count": count})
                else:
                    self.add(m, status="AMBIGUOUS", reason="no side is named")

    def negatives(self):
        for m in _NOT_PASSED.finditer(self.text):
            self.add(m, "is_passed_pawn", {"square": m.group(3) or m.group(4)}, polarity="negative", held="no longer" in m.group(0))
        for m in _NOT_PASSED_BARE.finditer(self.text):
            self.add(m, status="AMBIGUOUS", reason="which pawn is not stated")
        for m in _NOT_OPEN.finditer(self.text):
            self.add(m, "is_open_file", {"file": m.group(1)}, polarity="negative", held="no longer" in m.group(0))
        for m in _NEGATED.finditer(self.text):
            subject, form, verb, target = m.groups()[:5], m.group(6), m.group(7), m.groups()[7:]
            self._say(m, subject, verb, target, negative=True, held="longer" in form)
            then = _THEN_DOES.match(self.text, m.end())
            if then:
                self._say(then, then.groups()[1:], verb, target, at=m.start(), group=1)
        for m in _AFFIRMED.finditer(self.text):
            subject, verb, target = m.groups()[:5], m.group(6), m.groups()[6:]
            denied, denied_target = _THEN_DOES_NOT.match(self.text, m.end()), _THEN_NOT.match(self.text, m.end())
            if denied or denied_target:
                self._say(m, subject, verb, target)
            if denied:
                self._say(denied, denied.groups()[1:], verb, target, negative=True, at=m.start(), group=1)
            if denied_target:
                self._say(denied_target, subject, verb, denied_target.groups(), negative=True, at=m.start())

    def _say(self, m, subject, verb, target, negative=False, held=False, at=None, group=0):
        """One relation with its own polarity and its own span. The source is the written square, or the
        only such piece on the board: anything else is AMBIGUOUS, and the other clause still stands."""
        side, word, before, piece, after = subject
        color = word or (side.lower() if side else None)
        branch = board_at(self.text, self.fen, self.v1, m.start() if at is None else at)
        source = before or after or h._unique_piece(self.fen, branch, piece, color)
        t_color, t_piece, square, t_piece_after, t_bare = target
        square = square or h._unique_piece(self.fen, branch, t_bare) if t_bare else square
        if source is None or square is None:
            self.add(m, status="AMBIGUOUS", reason=f"which {piece if source is None else t_bare} is not settled", group=group)
            return
        self._relation(m, verb, source, square, piece, t_color, t_piece or t_piece_after, negative=negative, held=held, source_color=color, group=group)

    def material(self):
        taken = []

        def exact(m, side, amount):
            self.add(m, "material_balance_is", {"points": _balance(side, amount)})
            taken.append(m.span())
        for m in _POINTS_AFTER.finditer(self.text):
            exact(m, m.group(1), _number(m.group(2)) * h._DIRECTION[m.group(3)])
        for m in _POINTS_BEFORE.finditer(self.text):
            exact(m, m.group(1), _number(m.group(3)) * h._DIRECTION[m.group(2)])
        for m in _SIDED_SURPLUS.finditer(self.text):
            exact(m, m.group(1), _number(m.group(2)) * _LEAD[m.group(3)])
        for m in _ANY_SURPLUS.finditer(self.text):
            if not any(s <= m.start() and m.end() <= e for s, e in taken):
                self.add(m, status="AMBIGUOUS", reason="whose material this describes is not stated")
        for m in _STATED.finditer(self.text):
            self.add(m, "material_balance_is", {"points": int(re.sub(r"\s", "", m.group(1)).replace("−", "-"))})
        for m in h._EQUAL.finditer(self.text):
            self.add(m, "material_balance_is", {"points": 0}, polarity="negative" if m.group(1) else "positive")
        for pattern in (h._MATERIAL, h._LEAVES, h._SIDELESS):
            for m in pattern.finditer(self.text):
                self.add(m, status="UNSUPPORTED", reason=_RELATIVE_REASON)


class _Reader(h._HybridReader):
    def _stricter(self, c: HybridFact) -> HybridFact:
        """A claim is judged on the clause its subject stands in, so one clause's negation stays there."""
        clause = clause_of(self.text[c.span_start:c.span_end], c.fact)
        if clause is not None:
            c = c.model_copy(update={"span_start": c.span_start + clause[0], "span_end": c.span_start + clause[1], "sentence": self.text[c.span_start + clause[0]:c.span_start + clause[1]]})
            if c.polarity == "negative" and not h._NEGATION.search(c.sentence):
                return c.model_copy(update={"status": "AMBIGUOUS", "reason": "the negation in this sentence belongs to another clause", "fact": None, "guards": (), "change_from": None})
        return super()._stricter(c)


def rule_claims(text, fen, v1, source_id=""):
    chunks = split_chunks(text)
    limit = chunks[-1][1] if chunks else 0
    return [c for c in _Rules(text, fen, v1, source_id).run() if c.span_end <= limit]


def extract_hybrid(text: str, root_fen: str, backend: SemanticExtractorBackend, source_id: str = "") -> ExtractionV21:
    v1 = extract(text, root_fen, source_id)
    v2 = extract_v2(text, root_fen, backend, source_id, reader=_Reader)
    proposals = []
    for n, c in enumerate(f for f in v2.facts if not f.residual):
        if isinstance(c, HybridFact):
            proposals.append(c.model_copy(update={"candidate_ids": (f"v2:{n}",)}))
        else:  # a rejection for the whole chunk
            proposals.append(HybridFact(**c.model_dump(exclude={"fact", "guards", "change_from"}), produced_by=("v2",), candidate_ids=(f"v2:{n}",)))
    candidates = [checked(c, text) for c in h._from_v1(v1) + proposals + rule_claims(text, root_fen, v1, source_id)]
    facts = merge(text, candidates, root_fen, v1)
    return ExtractionV21(source_id=source_id, root_fen=root_fen, facts=tuple(facts), variations=v1.variations, backend={**v2.backend, "hybrid_version": VERSION})

"""Checks on every draft Friday writes (DJ06, PLAN-FINAL section 4 step 7). Pure functions: no model, no database.

1. Claims (`check`): every technology, employer or other proper name, number and job title in the draft must be on the facts
   the owner ticked (or his own form answers), or be a plain English word. Naming the role and the company of the post is not
   a claim, so the post's title, company and location count too. A technology the post asks for but the owner has not ticked
   is allowed only in a sentence that says it is a gap ("I have not used Kafka in production yet"). Words the owner allowed by
   hand count as facts. Any unknown word fails the draft, and the answer names it.
2. Style (`style`): no placeholder (the mail module's `placeholders.find`), no em dash, no AI-tell phrase, 120 to 200 words, no link or
   email address in the text (code adds the sign-off and the recipient). The writer rewrites once for these, then fails.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..assistant import placeholders
from . import postfacts
from .cv import KNOWN_SKILLS

MIN_WORDS, MAX_WORDS = 120, 200
MAX_SUBJECT = 100

# ---- technologies ----
_EXTRA_TECH = ("Next.js", "Nuxt", "Vue.js", "React.js", "ReactJS", "NodeJS", "NextJS", "k8s", "Golang", "JS", "TS", "Claude", "ChatGPT",
               "GPT", "Anthropic", "Hugging Face", "Gemini", "Llama", "Mistral", "Bedrock", "SageMaker", "Lambda", "EC2", "S3", "Heroku",
               "Netlify", "Cloudflare", "Prisma", "Sequelize", "TypeORM", "Mongoose", "Zustand", "MobX", "Storybook", "Remix", "Astro",
               "Bun", "Hono", "tRPC", "Zod", "Expo", "Jira", "Salesforce", "HubSpot", "Zapier", "Twilio", "Kibana", "Logstash",
               "ClickHouse", "Pinecone", "Weaviate", "pgvector", "LlamaIndex", "Temporal", "NATS", "Vitest", "Mocha", "Chai")
# English words that are also technologies: they count only in this exact casing (or in capitals).
AMBIGUOUS = {"Go", "R", "Rust", "Swift", "Spring", "Spring Boot", "Express", "Unity", "Spark", "REST", "React", "Rails", "Flask",
             "Dart", "Celery", "Electron", "Jest", "Pandas", "Snowflake", "Cypress", "Selenium", "Sentry", "Stripe", "Airflow", "Node",
             "RAG", "Bun", "Remix", "Astro", "Lambda", "Temporal", "Expo", "Mocha", "Chai", "Gemini", "Llama", "Mistral", "Claude",
             "Vercel", "Git", "JS", "TS", "Hono", "Zod", "NATS", "Bedrock"}
CANON = {"golang": "go", "postgresql": "postgres", "node.js": "node", "nodejs": "node", "llms": "llm", "react.js": "react",
         "reactjs": "react", "k8s": "kubernetes", "js": "javascript", "ts": "typescript", "nextjs": "next.js", "vue.js": "vue"}


def _canon(word: str) -> str:
    w = word.lower()
    return CANON.get(w, w)


def _tech_terms() -> list[tuple[str, re.Pattern, re.Pattern]]:
    """(canonical, strict pattern for drafts, loose pattern for facts). Longest first, so "React Native" wins over "React"."""
    seen: dict[str, str] = {}
    for t in (*postfacts.STACK_WORDS, *KNOWN_SKILLS, *_EXTRA_TECH):
        seen.setdefault(t.lower(), t)
    out = []
    for low, t in sorted(seen.items(), key=lambda kv: -len(kv[0])):
        if len(low) < 2:                 # "R" matches "R&D" and initials
            continue
        body = re.escape(t).replace(r"\ ", r"[\s-]+")
        edges = (r"(?<![\w.+#/])", r"(?![\w+#/]|\.\w)")
        nice = next((x for x in AMBIGUOUS if x.lower() == low), None)
        strict = re.compile(edges[0] + "(?:" + re.escape(nice) + "|" + re.escape(nice.upper()) + ")" + edges[1]) if nice else \
            re.compile(edges[0] + body + edges[1], re.I)
        out.append((_canon(low), strict, re.compile(edges[0] + body + edges[1], re.I)))
    return out


TECH = _tech_terms()
GAP_WORDS = re.compile(r"\b(?:not|never|no|yet|haven'?t|hasn'?t|didn'?t|new\s+to|learning|limited|little|less|gap|keen\s+to\s+learn)\b", re.I)

# ---- job titles ----
ROLE_WORDS = ("engineer", "developer", "programmer", "architect", "lead", "intern", "cto", "founder", "co-founder", "cofounder",
              "director", "head", "manager", "consultant", "sde", "swe", "sre")
# A role word right before one of these is a describing word, not a title: "developer tools", "engineer onboarding".
NOT_A_TITLE_BEFORE = {"tool", "tools", "tooling", "experience", "experiences", "platform", "platforms", "productivity", "relations",
                      "advocate", "advocacy", "community", "communities", "portal", "docs", "documentation", "ecosystem",
                      "onboarding", "workflow", "workflows", "program", "programs", "survey", "api", "apis", "console", "kit", "sdk"}
TITLE_MODIFIERS = {"senior", "sr", "staff", "principal", "junior", "jr", "lead", "chief", "full", "stack", "fullstack", "backend",
                   "back", "end", "frontend", "front", "software", "platform", "product", "founding", "ai", "ml", "web", "mobile",
                   "data", "devops", "tech", "technical", "engineering", "application", "applications", "infrastructure",
                   "cloud", "systems", "solutions", "research", "associate", "team", "i", "ii", "iii", "iv"}

# A name right after one of these is a claim about the owner's past. It needs a ticked fact, even when the post or its company names it.
_PAST_AT = re.compile(r"\b(?:worked\s+(?:at|for|with)|working\s+(?:at|for)|was\s+(?:at|with)|my\s+time\s+(?:at|with)|studied\s+at|"
                      r"graduated\s+from|years?\s+(?:at|with)|employed\s+(?:at|by)|interned\s+at|joined|left|came\s+from)\s+"
                      r"((?:[A-Z][\w&.'’-]*(?:\s+|(?=[,.;:!?]|$))){1,3})", re.M)

# ---- numbers ----
NUMBER = re.compile(r"(?<![\w.])[$₹€£]?\d+(?:[.,:]\d+)*(?:\s?%|\+|[kKxX]\b|\s?(?:lpa|LPA))?")
NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
                "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
                "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
                "ninety": 90, "hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000, "lakh": 100000,
                "crore": 10000000, "dozen": 12}
_NUMBER_WORD = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")s?\b", re.I)

# ---- plain English: capitalised words that are never a claim ----
PLAIN = set("""
a about above across after again against ago ahead all almost alongside also although always am among an and another any
anyone anything anyway are around as ask asked asking at available away back based be because been before being below
best better between beyond both brief briefly bring brings build builder builders building builds built busy but by call can
care careful cares chance change cheers clear clearly close closely code codebase come comes company content could
currently customer customers day days dear deep deeply design detail details did direct directly do does doing done down
during each early earlier easy either else end enjoy enjoyed enough even ever every everything example excellent experience
fact feature features few first fit fits fixed focus for forward found from full fun further get gets getting give given glad
go goal goals good great grew grow growing had happy hard has have having he hear hello help helped helping her here hey
hi high him his honest honestly hope how however i i'd i'll i'm i've idea if impact important in instead interest
interested into is it it's its job just keen kind know last later lately learn learned learning least less let let's like
liked little long look looking lot love made main mainly make makes making many may me mean meant meet might mine more
morning most mostly much my myself need needed needs new next nice no not note now of off often ok okay on once one only open
or other others our out outside over own owned owning part past people perhaps person plain plan please pleased point post
posted posting previously problem problems product products project projects quick quickly quite rather read reading ready
real really reason reasons recent recently regards related right role roles run running same say see seeing seen send sense
sent serious service services set share shared she ship shipped shipping short should show shown similar simple since
sincerely small so solid some someone something soon sort speak specific spent start started still such suit suits sure
system systems take talk team teams tell than thank thanks that that's the their them then there these they thing things
think this those though thought through time times to today together too tool tools took toward towards tried true truly
try trying two under understand understanding until up us use used useful user users using usual usually very want wanted
warm way ways we week weeks well went were what when where whether which while who whole why will wish with within without
work worked working works would write writing wrote year years yes yet you you'd you'll you're you've your yours
hiring careers career application applications apply applying engineering cv resume résumé portfolio remote hybrid onsite
full-time part-time contract freelance notice period salary visa sponsorship relocation interview interviews chat
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september
october november december
india indian ist utc gmt english hindi ai ml api apis saas b2b b2c ui ux ok pr prs mvp faq hn
""".split())
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9'’]*")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")

# ---- style ----
AI_TELLS = (
    r"i hope (?:this|my) (?:e-?mail |message |note )?finds you well", r"\bdelv(?:e|es|ed|ing)\b", r"passionate about",
    r"\bleverag(?:e|es|ed|ing)\b", r"i am writing to (?:express|apply)", r"i'?m writing to (?:express|apply)", r"\bthrilled\b",
    r"\besteemed\b", r"\bsynerg(?:y|ies|istic)\b", r"cutting[\s-]edge", r"game[\s-]?changer", r"\btapestry\b", r"\btestament to\b",
    r"\bseamless(?:ly)?\b", r"\brobust\b", r"dynamic team", r"fast[\s-]paced", r"perfect fit", r"look no further",
    r"rest assured", r"in today'?s", r"ever[\s-](?:evolving|changing)", r"\blandscape\b", r"\bunlock(?:s|ed|ing)?\b",
    r"\belevat(?:e|es|ed|ing)\b", r"\bspearhead(?:s|ed|ing)?\b", r"\bhoned?\b", r"navigat(?:e|ing) the", r"\bfurthermore\b",
    r"\bmoreover\b", r"\bin conclusion\b", r"not only\b[^.]{0,80}\bbut also\b", r"wealth of experience",
    r"proven track record", r"results[\s-]driven", r"detail[\s-]oriented", r"team player", r"go[\s-]getter",
    r"bring to the table", r"hit the ground running", r"at the intersection of", r"\bresonat(?:e|es|ed|ing)\b",
    r"\baligns? (?:perfectly |closely )?with\b", r"i am confident that", r"i'?m confident that", r"would be a great fit",
    r"\bexcited to (?:apply|join|bring)\b", r"\bempower(?:s|ed|ing)?\b", r"\bharness(?:es|ed|ing)?\b", r"\bfoster(?:s|ed|ing)?\b",
    r"\bmeticulous(?:ly)?\b", r"\bpivotal\b", r"\bunwavering\b", r"\bplethora\b", r"\bmyriad\b", r"\bkeen eye\b",
    r"thank you for (?:your )?(?:time and )?consideration", r"i would welcome the opportunity", r"\bholistic\b",
    r"\bit'?s not just\b", r"\bnot just [^.,]{1,40}, (?:it'?s|but)\b",
)
_AI_TELLS = [re.compile(p, re.I) for p in AI_TELLS]
_DASH = re.compile(r"[—―]|\s–\s|\s--\s")
_LINK = re.compile(r"https?://|www\.|\b[\w.-]+\.(?:com|io|dev|ai|app|in|org|net|co)\b(?:/\S*)?", re.I)
_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_WORD = re.compile(r"[\w'’.+#/-]+")


@dataclass
class Allowed:
    facts: list[str] = field(default_factory=list)       # ticked CV facts and the owner's form answers: he stands behind these
    context: list[str] = field(default_factory=list)     # the post's title, company and location: naming them is not a claim
    post_stack: list[str] = field(default_factory=list)  # technologies the post names: only in a sentence about a gap
    words: list[str] = field(default_factory=list)       # words the owner allowed by hand
    post_words: list[str] = field(default_factory=list)  # the post's fact lines. NOT trusted: a post can be written to steer a claim (DJ10 M2)


def _words_of(texts) -> set[str]:
    out = set()
    for t in texts:
        for w in _TOKEN.findall(t or ""):
            out.add(_base(w))
    return out


def _base(token: str) -> str:
    w = token.lower().replace("’", "'")
    return w[:-2] if w.endswith("'s") else w


def _numbers_in(texts) -> set[str]:
    out = set()
    for t in texts:
        for m in NUMBER.finditer(t or ""):
            out.add(_number_core(m.group(0)))
        for m in _NUMBER_WORD.finditer(t or ""):
            out.add(str(NUMBER_WORDS[m.group(1).lower()]))
    return out


def _number_core(s: str) -> str:
    core = re.sub(r"[^\d.:]", "", s.replace(",", ""))
    return core[:-2] if core.endswith(".0") else core.rstrip(".")


def _mask(text: str, a: int, b: int) -> str:
    return text[:a] + " " * (b - a) + text[b:]


def _sentence_at(text: str, pos: int) -> str:
    for m in _SENTENCE.finditer(text):
        if m.start() <= pos < m.end():
            return m.group(0)
    return ""


# A sentence that only reports what the post says ("Your post mentions Go and 15+ years") is not a claim about the owner (DJ14K).
_POST_REF = re.compile(r"\b(?:your|the)\s+(?:job\s+)?(?:post|posting|listing|job\s+description|description|advert|ad|role\s+description)\b"
                       r"[^.!?\n]{0,40}?\b(?:mentions?|asks?|lists?|says?|states?|names?|describes?|requires?|notes?|highlights?)\b", re.I)
_FIRST_PERSON = re.compile(r"\b(?:I|me|my|mine|myself|I'm|I've|I'd|I'll|we|our)\b", re.I)
_NUM_TOKEN = re.compile(r"\d[\d,.]*\+?%?")


def _num_key(tok: str) -> str:
    return tok.rstrip(".,").lower()


def _post_reference_spans(text: str, allowed: Allowed) -> list[tuple[int, int]]:
    """Inside a sentence that only cites the post ("Your post mentions Go and 15+ years"), the spans of the numbers and technologies that
    are in the post's own text as whole tokens. Only those spans are skipped. Every other word of the sentence (a school, a title, a
    company, a number or tool that is not in the post) is checked as before (DJ14KF M1). A sentence that speaks about the owner is not
    skipped at all."""
    post_text = " ".join([*allowed.post_words, *allowed.post_stack, *allowed.context])
    post_nums = {_num_key(n) for n in _NUM_TOKEN.findall(post_text)}
    post_tech = {_canon(t) for t in allowed.post_stack} | {c for c, _s, loose in TECH if loose.search(post_text.lower())}
    spans: list[tuple[int, int]] = []
    for m in _SENTENCE.finditer(text or ""):
        sent = m.group(0)
        if not _POST_REF.search(sent) or _FIRST_PERSON.search(sent):
            continue
        for n in _NUM_TOKEN.finditer(sent):
            if _num_key(n.group(0)) in post_nums:
                spans.append((m.start() + n.start(), m.start() + n.start() + len(n.group(0).rstrip(".,"))))
        for canon, strict, _loose in TECH:
            if canon in post_tech:
                spans.extend((m.start() + t.start(), m.start() + t.end()) for t in strict.finditer(sent))
    return spans


def check(text: str, allowed: Allowed) -> list[str]:
    """The unknown words in `text`, each named once, in order found. Empty means every claim is on the lists."""
    unknown: list[str] = []

    def bad(name: str) -> None:
        if name not in unknown:
            unknown.append(name)

    work = text or ""
    for a, b in _post_reference_spans(work, allowed):
        work = _mask(work, a, b)
    # 0. a name after "I worked at": only the owner's own facts count, never the post's title, company or lines (DJ10 M2)
    own = _words_of([*allowed.facts, *allowed.words])
    for m in _PAST_AT.finditer(work):
        for w in _TOKEN.findall(m.group(1)):
            if w[0].isupper() and _base(w) not in own and _base(w) not in PLAIN:
                bad(w)
    # Exact phrases first: the post's title, company and location, and phrases the owner allowed.
    for phrase in sorted({p.strip() for p in (*allowed.context, *allowed.words) if p and p.strip()}, key=len, reverse=True):
        for m in re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", work, re.I):
            work = _mask(work, m.start(), m.end())

    # 1. technologies
    fact_tech = {canon for canon, _s, loose in TECH if any(loose.search(f) for f in allowed.facts)}
    fact_tech |= {canon for canon, _s, loose in TECH if any(loose.search(w) for w in allowed.words)}
    post_tech = {_canon(t) for t in allowed.post_stack}
    for canon, strict, _loose in TECH:
        for m in list(strict.finditer(work)):
            if canon not in fact_tech and not (canon in post_tech and GAP_WORDS.search(_sentence_at(text, m.start()))):
                bad(m.group(0))
            work = _mask(work, m.start(), m.end())

    # 2. job titles: a role word and the title words before it must all come from one ticked role fact or the post's title
    sources = [_words_of([s]) for s in (*allowed.facts, *allowed.context, *allowed.words)]
    toks = list(_TOKEN.finditer(work))
    for i, t in enumerate(toks):
        low = _base(t.group(0))
        if low not in ROLE_WORDS:
            continue
        prev = _base(toks[i - 1].group(0)) if i else ""
        nxt = _base(toks[i + 1].group(0)) if i + 1 < len(toks) else ""
        if nxt in ROLE_WORDS and not work[t.end():toks[i + 1].start()].strip(" -"):
            continue                     # "Lead Engineer": the next role word takes this one as a title word
        if low == "lead" and not (t.group(0)[0].isupper() or prev in ("tech", "team")):
            continue
        if low == "head" and not (t.group(0)[0].isupper() and nxt == "of"):
            continue
        if (low == "manager" and prev == "hiring") or nxt in NOT_A_TITLE_BEFORE:
            continue
        start, words = i, [low]
        while start > 0 and len(words) < 4:
            p = toks[start - 1]
            between = work[p.end():toks[start].start()]
            pl = _base(p.group(0))
            if between.strip(" -") or not (pl in TITLE_MODIFIERS or (p.group(0)[0].isupper() and pl not in PLAIN)):
                break
            words.insert(0, pl)
            start -= 1
        if not any(set(words) <= s for s in sources):
            bad(work[toks[start].start():t.end()])
        work = _mask(work, toks[start].start(), t.end())

    # 3. numbers, in digits or in words ("one" is too common to be a claim)
    numbers = _numbers_in([*allowed.facts, *allowed.context, *allowed.words])
    for m in list(NUMBER.finditer(work)):
        if _number_core(m.group(0)) not in numbers:
            bad(m.group(0).strip())
        work = _mask(work, m.start(), m.end())
    for m in list(_NUMBER_WORD.finditer(work)):
        if str(NUMBER_WORDS[m.group(1).lower()]) not in numbers and m.group(1).lower() not in _words_of(allowed.facts):
            bad(m.group(0))
        work = _mask(work, m.start(), m.end())

    # 4. names: every capitalised word is plain English or on the lists (employers, schools, products, people, places)
    known = _words_of([*allowed.facts, *allowed.context, *allowed.words])        # not the post's lines (M2)
    for m in _TOKEN.finditer(work):
        w = m.group(0)
        if w[0].isupper() or any(c.isdigit() for c in w):
            b = _base(w)
            if b not in PLAIN and b not in known and b.removesuffix("s") not in ROLE_WORDS:   # step 2 judged the role words
                bad(w)
    return unknown


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))


def ai_tells(text: str) -> list[str]:
    return [m.group(0) for rx in _AI_TELLS for m in [rx.search(text or "")] if m]


def style(subject: str, body: str, recent_openers=(), sign_off: str = "") -> list[str]:
    """Problems the writer may fix with one rewrite. `body` is the whole letter; the owner's own `sign_off` at its end counts for
    the length only (he may put a link there)."""
    problems: list[str] = []
    letter = body[:len(body) - len(sign_off)] if sign_off and body.endswith(sign_off) else body
    both = f"{subject}\n{letter}"
    found = placeholders.find(both)
    if found:
        problems.append("It has a placeholder: " + ", ".join(found) + ".")
    if _DASH.search(both):
        problems.append("It has an em dash. Use a full stop or a comma.")
    tells = ai_tells(both)
    if tells:
        problems.append("It has phrases that sound machine-written: " + ", ".join(f'"{t}"' for t in tells) + ".")
    n = word_count(body)
    if not MIN_WORDS <= n <= MAX_WORDS:
        problems.append(f"It has {n} words. Write {MIN_WORDS} to {MAX_WORDS}.")
    if not subject.strip():
        problems.append("It has no subject line.")
    elif len(subject) > MAX_SUBJECT:
        problems.append(f"The subject is longer than {MAX_SUBJECT} characters.")
    if _EMAIL.search(both) or _LINK.search(both):
        problems.append("It has a link or an email address. Leave them out.")
    first = opener(body)
    if first and first in recent_openers:
        problems.append("It starts the same way as a recent draft. Start differently.")
    return problems


def opener(body: str) -> str:
    """The first five words after the greeting line, in lower case: used to vary the structure."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    if lines and len(lines[0].split()) <= 6 and lines[0].endswith((",", "!", ":")):
        lines = lines[1:]
    return " ".join(re.findall(r"[a-z0-9']+", " ".join(lines).lower())[:5])

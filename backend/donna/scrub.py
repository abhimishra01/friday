"""Scrub for the Sent-folder learner (DJ02). the mail module's `secrets_file.scrub` only hides the app password, so this is Friday's own.

It removes what must not be kept about other people: email addresses, links, phone numbers, street addresses, salary figures,
people's names and company names. The names come from the mail headers (every recipient, and the company in each recipient's
domain) and from patterns in the text (greetings, titles, "referred by ...", a first name from a common list).
Code does this, not a model: a model's output is scrubbed again here before anything is stored.
The owner's own name (from the From header) stays, because the sign-off is part of the owner's style.
"""
from __future__ import annotations

import re
import unicodedata
from email.utils import getaddresses

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))

EMAIL_RE = re.compile(r"[\w.+'%-]+@[\w-]+(?:\.[\w-]+)+")
OBFUSCATED_EMAIL_RE = re.compile(r"(?i)\b[\w.+-]+\s*[\[(]\s*at\s*[\])]\s*[\w.-]+\s*[\[(]\s*dot\s*[\])]\s*\w+")
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S*[^\s.,;:!?)\]>]")
BARE_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|org|net|io|in|co|ai|dev|app|me|xyz|info|biz|us|uk|de|fr|eu)\b(?:/\S*[^\s.,;:!?)\]>])?")
TECH_DOMAINS = {"asp.net", "vb.net", "node.js"}

CURRENCY = r"(?:₹|Rs\.?|INR|USD|US\$|\$|£|GBP|€|EUR|AUD|CAD|SGD|AED)"
NUM = r"\d[\d,]*(?:\.\d+)?"
UNIT = r"(?:\s?(?:k|m|mn|million|lakhs?|lacs?|lpa|cr|crores?)\b)?"
AMOUNT_RES = [
    re.compile(rf"(?i)(?<![\w])(?:{CURRENCY})\s?{NUM}{UNIT}"),
    re.compile(rf"(?i)\b{NUM}\s?(?:lpa|lakhs?|lacs?|crores?)\b"),
    re.compile(rf"(?i)\b{NUM}\s?(?:k|m)?\s?(?:/|per\s)\s?(?:year|yr|annum|month|mo|hour|hr|week|day)\b"),
    re.compile(rf"(?i)\b(?:ctc|salary|salaries|compensation|package|stipend|pay|rate|remuneration|income|earning)\b[^\n.;]{{0,40}}?{NUM}{UNIT}"),
    re.compile(rf"(?i)\b{NUM}{UNIT}\s?(?:inr|usd|eur|gbp|aud|cad|sgd|aed|rupees|dollars|pounds|euros)\b"),
]

PHONE_CANDIDATE_RE = re.compile(r"(?<![\w/])\+?\(?\d[\d\s().-]{6,}\d(?![\w/])")
DATE_LIKE_RE = re.compile(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$|^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}$")

STREET_WORDS = (r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Nagar|Marg|Colony|Sector|Way|Court|Ct|Place|Pl|"
                r"Square|Sq|Park|Block|Phase|Floor|Tower|Towers|Apartments?|Society|Chowk|Enclave|Extension|Vihar|Puram|Highway|Hwy)")
ADDRESS_RES = [
    re.compile(r"(?im)^\s*(?:address|addr|location|residence|office address|postal address)\s*:.*$"),
    re.compile(rf"\b(?:(?:Flat|House|Plot|Door|Apt|Unit|Suite|H\.?\s?No\.?)\s*(?:No\.?\s*)?[\w/-]+,?\s+)?\d{{1,5}}[A-Za-z]?(?:[-/]\d+)?,?\s+"
               rf"(?:[A-Z][\w.'-]*\s+){{0,4}}{STREET_WORDS}\b\.?"
               rf"(?:,\s*[A-Z][\w'-]*(?:\s[A-Z][\w'-]*){{0,2}}){{0,3}}(?:,?\s*\d{{5,6}}(?:-\d{{4}})?)?"),
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b"),
    re.compile(r"(?i)\b(?:pin(?:\s?code)?|zip(?:\s?code)?|postcode|pincode)\s*[:\-]?\s*\d{5,6}\b"),
]

TITLE_NAME_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Shri|Smt|Sri|Sir|Madam)\.?\s+[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?")
GREETING_RE = re.compile(r"(?im)^(\s*(?:hi|hello|dear|hey|hiya|greetings|good (?:morning|afternoon|evening))[,\s]+)(.+?)([,!:.]?\s*)$")
GREETING_GENERIC = re.compile(r"(?i)^(?:there|team|all|everyone|everybody|hiring|recruit|recruiting|recruiter|talent|careers|sir|madam|sir or madam|"
                              r"sir/madam|folks|colleagues|people|hr|human resources)\b|[\[{]name[\]}]")
INTRO_RE = re.compile(r"(?i:\b(?:referred|introduced|recommended|forwarded|suggested|connected|told|advised)\s+(?:me\s+)?(?:by|to|with)\s+"
                      r"|\b(?:spoke|talked|chatted|met|worked|speaking|talking|meeting)\s+(?:with|to)\s+"
                      r"|\b(?:cc|cc'd|attention|attn|care of|c/o)\s*:?\s+)"
                      r"((?:Mr|Mrs|Ms|Dr)\.?\s+)?([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,2})")
COMPANY_RE = re.compile(r"\b(?:at|join(?:ing)?)[ ]+(?:the[ ]+)?([A-Z][\w&'-]*(?:[ ][A-Z][\w&'-]*){0,2})(?![\w&'-])")
COMPANY_SUFFIX_RE = re.compile(r"\b[A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*){0,2}\s+(?:Inc|LLC|Ltd|Limited|Pvt|Corp|Corporation|GmbH|Technologies|"
                               r"Technology|Labs|Systems|Solutions|Software|Softwares|Group|Consulting|Studios)\b")

ROLE_WORDS = {"hr", "team", "careers", "career", "jobs", "job", "hiring", "talent", "recruit", "recruiting", "recruiter", "recruitment",
              "people", "join", "work", "apply", "applications", "application", "the", "and", "mr", "mrs", "ms", "dr", "info", "hello",
              "contact", "support", "admin", "office", "noreply", "reply", "mail", "email", "inbox", "manager", "head", "lead", "sales"}
GENERIC_MAIL = {"gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "ymail", "icloud", "me", "proton", "protonmail", "aol",
                "rediffmail", "zoho", "mail", "msn", "gmx", "yandex", "fastmail", "hey", "tutanota"}
ORG_WORDS = re.compile(r"(?i)\b(?:careers?|jobs?|recruit\w*|talent|hr|hiring|team|inc|llc|ltd|limited|pvt|corp|corporation|gmbh|technologies|"
                       r"technology|labs|systems|solutions|software|group|consulting|studios|people)\b")

# Given names that are not ordinary words. A match is followed by one capitalised word (the family name), which goes too.
FIRST_NAMES = frozenset("""
aarav aarti aaditya abhinav abhijit aditi aditya ajay ajit akash akshay alok amar amit amita amol anand ananya anil anirudh anita anjali ankit
ankita ankur anshul anurag anuj anusha apoorva archana arjun arun aruna arvind aryan ashish ashok ashwin atul avinash ayush bharat bhavna
bhavya chandan chetan darshan deepa deepak deepika dev devesh dhruv dinesh divya farhan faisal gagan gaurav gautam geeta gopal gaurang govind
gunjan hardik harish harsh harsha hemant himanshu imran isha ishaan jaideep jatin jay jayant jitendra juhi kabir kailash kajal kamal kamlesh
karan karthik kartik kavita kavya keshav kiran kishore krishna kunal lakshmi lalit lata madhav mahesh mahendra manas manish manoj mansi
mayank meena meera megha mohan mohit mukesh mukul nandini naveen navin neelam neeraj neha nidhi nikhil nikita nilesh nisha nitin nitish
om pallavi pankaj parth pooja prabhat pradeep pranav prasad prashant pratik praveen preeti prerna priya priyanka punit puneet radha
raghav rahul raj rajat rajeev rajesh rajiv rakesh ramesh rani ranjit rashmi ravi reena rekha renu richa riya rohan rohit roshni sachin sagar
sahil sakshi salman sameer sandeep sanjana sanjay sanjeev sanket santosh sapna saurabh shailesh shalini shankar sharad shashank shefali
shilpa shivam shreya shruti shubham siddharth smita sneha sohan sonal sonam sourav srinivas subhash sudhir sujata sumit sunil sunita
suraj suresh sushant swati tanmay tanvi tarun tejas trisha tushar uday uma umesh vaibhav vandana varun vasant vidya vijay vikas vikram
vinay vinod vipin vishal vishnu vivek yash yogesh yusuf zoya
adam alan alex alexander alice amanda amy andrew angela anna anthony barbara benjamin brian bruce carol catherine charles charlotte chloe
chris christine christopher claire daniel danielle david deborah diana donald dorothy douglas edward elena elizabeth emily emma eric ethan
frank gabriel gary george gregory hannah harry heather helen henry isabella jack jacob james jane jason jennifer jeremy jessica joan joe
john jonathan joseph joshua julia julie justin karen katherine kathleen kevin kimberly kyle laura lauren lawrence linda lisa lucy margaret
maria mary matthew megan melissa michael michelle natalie nathan nicholas nicole olivia oliver patricia patrick paul peter philip rachel
raymond rebecca richard robert roger ronald ruth ryan samantha samuel sandra sarah scott sean sharon simon sophie stephanie stephen steven
susan tamara teresa theresa thomas timothy tracy tyler victoria vincent walter wayne zachary
""".split())
FIRST_NAME_RE = re.compile(r"\b(" + "|".join(sorted((n.capitalize() for n in FIRST_NAMES), key=len, reverse=True)) + r")\b(?:\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?)?")

PH = {"email": "[email]", "link": "[link]", "phone": "[phone]", "address": "[address]", "amount": "[amount]", "name": "[name]",
      "company": "[company]"}


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'-]{1,}", text)


def _token_re(tokens) -> re.Pattern | None:
    """A name token matches as `Priya` or `PRIYA`, never as a lower-case word (so a person named Will does not remove every "will")."""
    forms = sorted({f for t in tokens for f in (t.capitalize(), t.upper())}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(f) for f in forms) + r")\b") if forms else None


def _loose_re(label: str) -> re.Pattern:
    """`acmecorp` also matches `Acme Corp`, `acme-corp` and `AcmeCorp`."""
    return re.compile(r"(?i)\b" + r"[\s.\-_]?".join(re.escape(c) for c in label) + r"\b")


class Scrubber:
    def __init__(self, owner_names=()):
        self.keep = {t.lower() for n in owner_names for t in _words(n)}
        self.names: set[str] = set()
        self.companies: set[str] = set()        # phrases, such as "Acme Corp"
        self.labels: set[str] = set()           # domain labels, such as "acmecorp"
        self._compiled = None

    # ---- what we learn from headers ----
    def learn_recipients(self, header_values: list[str]) -> None:
        """Every To, Cc and Bcc value of a mail: display names, local parts and the company in the domain."""
        for display, addr in getaddresses(header_values):
            self.learn_person(display, addr)

    def learn_person(self, display: str, addr: str) -> None:
        display = (display or "").strip().strip("\"'")
        local, _, domain = (addr or "").lower().partition("@")
        if display and ORG_WORDS.search(display):
            phrase = " ".join(w for w in _words(display) if w.lower() not in ROLE_WORDS)
            if len(phrase) >= 3:
                self.companies.add(phrase)
        else:
            self.names.update(w for w in _words(display) if len(w) >= 3)
        self.names.update(w for w in re.split(r"[^a-z]+", local) if len(w) >= 4)
        labels = domain.split(".")
        label = labels[-2] if len(labels) >= 2 else ""
        if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov"):
            label = labels[-3]
        if label and label not in GENERIC_MAIL and len(label) >= 3:
            self.labels.add(label)
        self._compiled = None

    def _build(self):
        names = {n for n in self.names if n.lower() not in self.keep and n.lower() not in ROLE_WORDS and n.lower() not in GENERIC_MAIL}
        self._compiled = (_token_re(names),
                          [re.compile(r"(?i)\b" + r"\s+".join(re.escape(w) for w in p.split()) + r"\b")
                           for p in sorted(self.companies, key=len, reverse=True)],
                          [_loose_re(lab) for lab in sorted(self.labels, key=len, reverse=True) if lab not in self.keep],
                          [_token_re([lab]) for lab in self.labels if len(lab) <= 5 and lab not in self.keep])
        return self._compiled

    # ---- the scrub ----
    def clean(self, text: str) -> str:
        t = unicodedata.normalize("NFKC", text or "").translate(ZERO_WIDTH)
        t = EMAIL_RE.sub(PH["email"], t)
        t = OBFUSCATED_EMAIL_RE.sub(PH["email"], t)
        t = URL_RE.sub(PH["link"], t)
        t = BARE_DOMAIN_RE.sub(lambda m: m.group(0) if m.group(0).lower() in TECH_DOMAINS else PH["link"], t)
        for rx in AMOUNT_RES:
            t = rx.sub(PH["amount"], t)
        t = self._phones(t)
        for rx in ADDRESS_RES:
            t = rx.sub(PH["address"], t)
        t = GREETING_RE.sub(self._greeting, t)
        t = TITLE_NAME_RE.sub(PH["name"], t)
        t = INTRO_RE.sub(self._intro, t)
        t = FIRST_NAME_RE.sub(lambda m: m.group(0) if m.group(1).lower() in self.keep else PH["name"], t)
        t = COMPANY_SUFFIX_RE.sub(PH["company"], t)
        names, phrases, loose, short = self._compiled or self._build()
        for rx in phrases:
            t = rx.sub(PH["company"], t)
        for rx in loose:
            t = rx.sub(PH["company"], t)
        for rx in short:
            if rx:
                t = rx.sub(PH["company"], t)
        if names:
            t = names.sub(PH["name"], t)
        t = COMPANY_RE.sub(self._company, t)
        return t

    def _intro(self, m: re.Match) -> str:
        """"referred by Rahul Verma" -> "referred by [name]"."""
        start = m.start(1) if m.group(1) else m.start(2)
        if all(w.lower() in self.keep for w in _words(m.group(2))):
            return m.group(0)
        return m.group(0)[:start - m.start(0)] + PH["name"]

    def _company(self, m: re.Match) -> str:
        """"a role at Acme Corp" -> "a role at [company]". Skills and ordinary capitalised words stay."""
        who = m.group(1)
        first = who.split()[0]
        if first.lower() in self.keep or first in COMPANY_SAFE or first.lower() in COMPANY_SAFE or who.startswith("["):
            return m.group(0)
        return m.group(0)[:m.start(1) - m.start(0)] + PH["company"]

    def _greeting(self, m: re.Match) -> str:
        who = m.group(2).strip()
        if GREETING_GENERIC.match(who) or re.search(r"[\[{]", who):
            return m.group(0)
        if all(w.lower() in self.keep for w in _words(who)):
            return m.group(0)
        return m.group(1) + PH["name"] + m.group(3)

    @staticmethod
    def _phones(t: str) -> str:
        def one(m: re.Match) -> str:
            s = m.group(0)
            digits = re.sub(r"\D", "", s)
            if not 8 <= len(digits) <= 15 or DATE_LIKE_RE.match(s.strip()):
                return s
            return PH["phone"]
        return PHONE_CANDIDATE_RE.sub(one, t)

    # ---- the fail-safe: what is still in a text after clean() ----
    def leaks(self, text: str) -> list[str]:
        out = []
        if EMAIL_RE.search(text) or OBFUSCATED_EMAIL_RE.search(text):
            out.append("email")
        if URL_RE.search(text):
            out.append("link")
        if any(8 <= len(re.sub(r"\D", "", m.group(0))) <= 15 and not DATE_LIKE_RE.match(m.group(0).strip()) for m in PHONE_CANDIDATE_RE.finditer(text)):
            out.append("phone")
        if any(rx.search(text) for rx in AMOUNT_RES[:3]):
            out.append("amount")
        if any(rx.search(text) for rx in ADDRESS_RES[1:3]):
            out.append("address")
        names, phrases, loose, short = self._compiled or self._build()
        if (names and names.search(text)) or any(rx.search(text) for rx in phrases + loose):
            out.append("name")
        return out


# Capitalised words after "at", "join" or "with" that are not company names.
COMPANY_SAFE = {"the", "a", "an", "my", "our", "your", "least", "most", "once", "first", "last", "home", "work", "scale", "speed", "this",
                "that", "these", "those", "all", "each", "every", "any", "no", "some", "present", "times", "night", "weekends", "short",
                "I", "It", "We", "You", "They", "He", "She", "Python", "Java", "JavaScript", "TypeScript", "React", "Node", "Django", "Flask",
                "FastAPI", "AWS", "Azure", "GCP", "Docker", "Kubernetes", "SQL", "PostgreSQL", "MySQL", "Redis", "Git", "GitHub", "Linux",
                "Spring", "Go", "Rust", "Kotlin", "Swift", "Angular", "Vue", "Next", "Express", "Celery", "Kafka", "Terraform", "Jenkins"}

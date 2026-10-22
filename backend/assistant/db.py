"""The two mail helpers Donna uses."""
from __future__ import annotations

import re

EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


def norm_email(s: str) -> str:
    return (s or "").strip().lower()

from __future__ import annotations

import re

ALCOHOL_TERMS = {
    "alcohol",
    "beer",
    "lager",
    "ale",
    "stout",
    "wine",
    "pinot",
    "shiraz",
    "merlot",
    "cabernet",
    "chardonnay",
    "sauvignon",
    "prosecco",
    "champagne",
    "cocktail",
    "pisco",
    "mezcal",
    "aperol",
    "campari",
    "vermouth",
    "liqueur",
    "margarita",
    "martini",
    "negroni",
    "manhattan",
    "mojito",
    "daiquiri",
    "paloma",
    "gimlet",
    "sangria",
    "mimosa",
    "bellini",
    "caipirinha",
    "spritz",
    "vodka",
    "gin",
    "rum",
    "whisky",
    "whiskey",
    "bourbon",
    "tequila",
    "sake",
    "cider",
    "ipa",
    "asahi",
    "tiger",
    "balter",
    "stomping",
    "pint",
    "schooner",
}

ALCOHOL_PHRASES = {
    "pisco sour",
    "old fashioned",
    "espresso martini",
    "pina colada",
    "piña colada",
    "bloody mary",
    "moscow mule",
    "dark and stormy",
    "gin and tonic",
    "vodka tonic",
    "aperol spritz",
    "stomping ground",
    "sailors grave",
}

KNOWN_ALCOHOL_ITEM_NAMES = {
    "canta",
}


def is_alcohol(description: str) -> bool:
    normalized = description.lower()
    normalized = re.sub(r"[^a-z0-9ñ]+", " ", normalized).strip()
    if normalized in KNOWN_ALCOHOL_ITEM_NAMES:
        return True
    if any(phrase in normalized for phrase in ALCOHOL_PHRASES):
        return True
    if re.search(r"\basa\w{2,8}\b", normalized):
        return True
    words = set(re.findall(r"[a-z]+", normalized))
    return bool(words & ALCOHOL_TERMS)

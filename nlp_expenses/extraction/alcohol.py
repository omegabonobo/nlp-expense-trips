from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class AlcoholDetection:
    """Explainable, precision-first classification for a receipt line."""

    is_alcohol: bool
    confidence: float
    reason: str
    matched_term: str = ""


# These phrases take precedence over product/category matches unless the line
# explicitly supplies a non-zero ABV. They prevent common menu false positives.
NON_ALCOHOL_PATTERNS = {
    "explicitly non-alcoholic": re.compile(
        r"\b(?:non\s*alcoholic|alcohol\s*free|zero\s*alcohol|zero\s*proof|0[.,]0+|0(?:[.,]0+)?\s*%\s*abv)\b"
    ),
    "non-alcoholic drink style": re.compile(r"\b(?:mocktail|virgin)\b"),
    "non-alcoholic product": re.compile(
        r"\b(?:ginger beer|root beer|birch beer|butter beer|beer batter(?:ed)?|cooking wine|wine vinegar)\b"
    ),
    "meat cut containing spirit name": re.compile(r"\bscotch\s+(?:fillet|filet|steak|beef)\b"),
    "receipt tax or charge": re.compile(
        r"^(?:gst|hst|qst|vat|sales\s+tax|tax|service\s+(?:fee|charge)|"
        r"weekend\s+surcharge|card\s+surcharge|gratuity|tip|discount)\b"
    ),
}

ALCOHOL_CATEGORY_PHRASES = {
    "alcoholic ginger beer",
    "alcoholic lemonade",
    "barley wine",
    "dessert wine",
    "fortified wine",
    "hard cider",
    "hard lemonade",
    "hard seltzer",
    "india pale ale",
    "malt liquor",
    "pale ale",
    "red wine",
    "rice wine",
    "rose wine",
    "rosé wine",
    "sparkling wine",
    "white wine",
}

ALCOHOL_COCKTAIL_PHRASES = {
    "aperol spritz",
    "bee s knees",
    "between the sheets",
    "black russian",
    "bloody mary",
    "brandy crusta",
    "canchanchara",
    "champagne cocktail",
    "chartreuse swizzle",
    "clover club",
    "corpse reviver",
    "cuba libre",
    "dark and stormy",
    "espresso martini",
    "french 75",
    "french connection",
    "gin and tonic",
    "gin tonic",
    "hemingway special",
    "horse s neck",
    "irish coffee",
    "john collins",
    "long island iced tea",
    "mai tai",
    "mary pickford",
    "mint julep",
    "moscow mule",
    "naked and famous",
    "new york sour",
    "old fashioned",
    "paper plane",
    "pina colada",
    "piña colada",
    "pisco sour",
    "planter s punch",
    "porto flip",
    "ramos fizz",
    "russian spring punch",
    "rusty nail",
    "sea breeze",
    "sex on the beach",
    "singapore sling",
    "south side",
    "tequila sunrise",
    "tommy s margarita",
    "vodka and tonic",
    "vodka tonic",
    "whiskey sour",
    "whisky sour",
    "white lady",
    "white russian",
}

# Multi-word brands are safer than isolated tokens such as "Stella", "Bombay",
# "Malibu", or "Patron", which also occur in ordinary merchant/menu text.
ALCOHOL_BRAND_PHRASES = {
    "bombay sapphire",
    "captain morgan",
    "carlton draught",
    "chateau ste michelle",
    "don julio",
    "four pines",
    "grey goose",
    "havana club",
    "jack daniel s",
    "jack daniels",
    "james squire",
    "jim beam",
    "johnnie walker",
    "jose cuervo",
    "little creatures",
    "maker s mark",
    "mountain goat",
    "sailors grave",
    "stella artois",
    "stomping ground",
    "stone and wood",
    "stone wood",
    "tooheys new",
    "victoria bitter",
    "wild turkey",
    "xxxx gold",
    "young henrys",
}

# Only terms that are reasonably specific on a purchased line belong here.
# Ambiguous serving words such as "glass", "bottle", "pint", and "schooner"
# are intentionally not sufficient on their own.
ALCOHOL_TERMS = {
    # General categories
    "alcohol",
    "aperitif",
    "aperol",
    "beer",
    "campari",
    "cider",
    "cocktail",
    "digestif",
    "liqueur",
    "spirit",
    "spirits",
    "wine",
    # Beer styles
    "ale",
    "barleywine",
    "bock",
    "doppelbock",
    "dubbel",
    "eisbock",
    "gose",
    "gueuze",
    "hefeweizen",
    "helles",
    "ipa",
    "kolsch",
    "lambic",
    "lager",
    "maibock",
    "pilsener",
    "pilsner",
    "porter",
    "quadrupel",
    "rauchbier",
    "saison",
    "schwarzbier",
    "stout",
    "tripel",
    "weizen",
    "witbier",
    # Wine grapes and styles
    "barbera",
    "beaujolais",
    "cabernet",
    "cava",
    "champagne",
    "chardonnay",
    "chianti",
    "gewurztraminer",
    "grenache",
    "madeira",
    "malbec",
    "merlot",
    "moscato",
    "pinot",
    "prosecco",
    "riesling",
    "sangiovese",
    "sauvignon",
    "semillon",
    "sherry",
    "shiraz",
    "syrah",
    "tempranillo",
    "vermouth",
    "viognier",
    "zinfandel",
    # Spirits
    "absinthe",
    "aguardiente",
    "amaretto",
    "amaro",
    "armagnac",
    "aquavit",
    "baijiu",
    "bourbon",
    "brandy",
    "cachaca",
    "cognac",
    "gin",
    "grappa",
    "limoncello",
    "mezcal",
    "ouzo",
    "pisco",
    "rum",
    "sake",
    "sambuca",
    "schnapps",
    "scotch",
    "soju",
    "tequila",
    "vodka",
    "whiskey",
    "whisky",
    # Distinctive cocktails
    "bellini",
    "boulevardier",
    "caipirinha",
    "cosmopolitan",
    "daiquiri",
    "gimlet",
    "margarita",
    "manhattan",
    "martini",
    "mimosa",
    "mojito",
    "negroni",
    "paloma",
    "sangria",
    "sazerac",
    "sidecar",
    "spritz",
    "zombie",
    # Distinctive brands commonly seen on restaurant receipts
    "absolut",
    "ardbeg",
    "asahi",
    "bacardi",
    "baileys",
    "balter",
    "beefeater",
    "becks",
    "belvedere",
    "budweiser",
    "bulmers",
    "carlsberg",
    "chartreuse",
    "chimay",
    "chivas",
    "cointreau",
    "coopers",
    "corona",
    "dewars",
    "disaronno",
    "duvel",
    "erdinger",
    "furphy",
    "glenfiddich",
    "glenlivet",
    "guinness",
    "heineken",
    "hennessy",
    "hendricks",
    "hoegaarden",
    "jagermeister",
    "jameson",
    "kahlua",
    "kilkenny",
    "kirin",
    "lagavulin",
    "leffe",
    "macallan",
    "magners",
    "modelo",
    "paulaner",
    "peroni",
    "rekorderlig",
    "sapporo",
    "smirnoff",
    "somersby",
    "strongbow",
    "talisker",
    "tanqueray",
    "tooheys",
}

# Some products are safe only when the whole cleaned line is the item name.
KNOWN_ALCOHOL_ITEM_NAMES = {
    "alexander",
    "angel face",
    "aviation",
    "bramble",
    "cardinale",
    "canta",
    "casino",
    "east skipper",
    "grasshopper",
    "paradise",
    "penicillin",
    "strawberry fields",
    "tuxedo",
}

# Common OCR variants observed in the project's source receipts.
OCR_ALCOHOL_ALIASES = {
    "asaht": "asahi",
    "asahl": "asahi",
    "asahtml": "asahi",
}

OCR_ALCOHOL_PHRASES = {
    "strawberry fielss": "strawberry fields",
    "tiger sche": "tiger schooner",
}

# A bare percentage is normally tax, a surcharge, or a discount on a receipt.
# Require an explicit alcohol marker; named drinks such as "Corona 5%" are
# still caught by the product/category rules below.
ABV_RE = re.compile(r"\b(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:abv|alc(?:ohol)?(?:\s*/\s*vol)?)\b")


def normalize_alcohol_text(description: str) -> str:
    decomposed = unicodedata.normalize("NFKD", description or "")
    ascii_text = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    normalized = ascii_text.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9%.,]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip(" .,")


def detect_alcohol(description: str) -> AlcoholDetection:
    """Classify a receipt line and retain the evidence used for the decision."""

    normalized = normalize_alcohol_text(description)
    if not normalized:
        return AlcoholDetection(False, 1.0, "empty description")

    abv_matches = [float(value.replace(",", ".")) for value in ABV_RE.findall(normalized)]
    if any(value > 0.5 for value in abv_matches):
        value = next(value for value in abv_matches if value > 0.5)
        return AlcoholDetection(True, 0.99, "explicit non-zero alcohol content", f"{value:g}%")

    for reason, pattern in NON_ALCOHOL_PATTERNS.items():
        match = pattern.search(normalized)
        if match:
            return AlcoholDetection(False, 0.99, reason, match.group(0))

    phrase_groups = (
        ("alcohol category", ALCOHOL_CATEGORY_PHRASES, 0.97),
        ("recognized cocktail", ALCOHOL_COCKTAIL_PHRASES, 0.97),
        ("recognized alcohol brand", ALCOHOL_BRAND_PHRASES, 0.95),
    )
    for reason, phrases, confidence in phrase_groups:
        matched = _first_phrase_match(normalized, phrases)
        if matched:
            return AlcoholDetection(True, confidence, reason, matched)

    for phrase, corrected in OCR_ALCOHOL_PHRASES.items():
        if f" {phrase} " in f" {normalized} ":
            return AlcoholDetection(
                True, 0.91, "recognized OCR variant of alcoholic menu item", corrected
            )

    words = re.findall(r"[a-z0-9]+", normalized)
    for word in words:
        alias = OCR_ALCOHOL_ALIASES.get(word)
        if alias:
            return AlcoholDetection(True, 0.91, "recognized OCR variant of alcohol brand", alias)

    matched_terms = sorted(
        set(words) & ALCOHOL_TERMS, key=lambda value: (words.index(value), value)
    )
    if matched_terms:
        matched = matched_terms[0]
        return AlcoholDetection(
            True, 0.93, "recognized alcohol term, style, cocktail, or brand", matched
        )

    item_name = _without_leading_quantity(normalized)
    if item_name in KNOWN_ALCOHOL_ITEM_NAMES:
        return AlcoholDetection(True, 0.90, "known alcoholic menu item", item_name)

    # Rosé loses its accent during normalization and "rose" is otherwise too
    # ambiguous to use as a general token.
    if re.fullmatch(r"(?:(?:glass|gls|btl|bottle)\s+)?rose(?:\s+\d{2,4})?", item_name):
        return AlcoholDetection(True, 0.87, "wine-style receipt label", "rose")

    return AlcoholDetection(False, 0.80, "no alcohol evidence")


def is_alcohol(description: str) -> bool:
    """Compatibility wrapper for existing extractors and workbook generation."""

    return detect_alcohol(description).is_alcohol


def _first_phrase_match(normalized: str, phrases: set[str]) -> str:
    padded = f" {normalized} "
    for phrase in sorted(phrases, key=lambda value: (-len(value), value)):
        normalized_phrase = normalize_alcohol_text(phrase)
        if f" {normalized_phrase} " in padded:
            return phrase
    return ""


def _without_leading_quantity(normalized: str) -> str:
    return re.sub(r"^\d+\s*(?:x\s*)?", "", normalized).strip()

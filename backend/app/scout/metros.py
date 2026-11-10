"""Major US metro areas, for "open to relocating to a major metro".

The roughly fifty largest metro areas, by their main cities and the suburbs job postings name most
(Mountain View for the Bay Area, Bellevue for Seattle), each with its state. A posting is in a
major metro when any place it names is one of these: the city, and its state when the posting
gives one. Fixed data, so the answer is the same every run and costs nothing.
"""

import re

# City and state, lower case. Several cities can stand for one metro.
_METRO_CITIES: tuple[tuple[str, str], ...] = (
    # New York
    ("new york", "ny"), ("new york city", "ny"), ("nyc", "ny"), ("manhattan", "ny"),
    ("brooklyn", "ny"), ("queens", "ny"), ("jersey city", "nj"), ("newark", "nj"),
    ("hoboken", "nj"), ("stamford", "ct"), ("white plains", "ny"),
    # Los Angeles
    ("los angeles", "ca"), ("santa monica", "ca"), ("culver city", "ca"), ("pasadena", "ca"),
    ("burbank", "ca"), ("long beach", "ca"), ("irvine", "ca"), ("el segundo", "ca"),
    ("anaheim", "ca"), ("costa mesa", "ca"),
    # Chicago
    ("chicago", "il"), ("evanston", "il"), ("schaumburg", "il"), ("naperville", "il"),
    # Dallas-Fort Worth
    ("dallas", "tx"), ("fort worth", "tx"), ("plano", "tx"), ("irving", "tx"),
    ("frisco", "tx"), ("arlington", "tx"),
    # Houston
    ("houston", "tx"), ("the woodlands", "tx"), ("sugar land", "tx"),
    # Washington
    ("washington", "dc"), ("arlington", "va"), ("alexandria", "va"), ("reston", "va"),
    ("mclean", "va"), ("tysons", "va"), ("herndon", "va"), ("bethesda", "md"),
    ("rockville", "md"), ("silver spring", "md"),
    # Philadelphia
    ("philadelphia", "pa"), ("king of prussia", "pa"), ("conshohocken", "pa"),
    ("camden", "nj"), ("wilmington", "de"), ("cherry hill", "nj"),
    # Miami
    ("miami", "fl"), ("fort lauderdale", "fl"), ("boca raton", "fl"), ("west palm beach", "fl"),
    # Atlanta
    ("atlanta", "ga"), ("alpharetta", "ga"), ("marietta", "ga"),
    # Boston
    ("boston", "ma"), ("cambridge", "ma"), ("somerville", "ma"), ("waltham", "ma"),
    ("burlington", "ma"), ("quincy", "ma"),
    # Phoenix
    ("phoenix", "az"), ("scottsdale", "az"), ("tempe", "az"), ("chandler", "az"),
    ("mesa", "az"),
    # San Francisco Bay Area and San Jose
    ("san francisco", "ca"), ("oakland", "ca"), ("berkeley", "ca"), ("san mateo", "ca"),
    ("redwood city", "ca"), ("menlo park", "ca"), ("palo alto", "ca"), ("mountain view", "ca"),
    ("sunnyvale", "ca"), ("santa clara", "ca"), ("san jose", "ca"), ("cupertino", "ca"),
    ("south san francisco", "ca"), ("emeryville", "ca"), ("san bruno", "ca"),
    ("foster city", "ca"), ("fremont", "ca"),
    # Riverside, Detroit, Seattle, Minneapolis, San Diego, Tampa, Denver
    ("riverside", "ca"), ("san bernardino", "ca"),
    ("detroit", "mi"), ("ann arbor", "mi"), ("troy", "mi"),
    ("seattle", "wa"), ("bellevue", "wa"), ("redmond", "wa"), ("kirkland", "wa"),
    ("tacoma", "wa"),
    ("minneapolis", "mn"), ("saint paul", "mn"), ("st. paul", "mn"), ("st paul", "mn"),
    ("san diego", "ca"), ("carlsbad", "ca"), ("la jolla", "ca"),
    ("tampa", "fl"), ("st. petersburg", "fl"), ("st petersburg", "fl"), ("clearwater", "fl"),
    ("denver", "co"), ("boulder", "co"), ("aurora", "co"), ("englewood", "co"),
    # Baltimore, St. Louis, Orlando, Charlotte, San Antonio, Portland, Sacramento, Pittsburgh
    ("baltimore", "md"), ("columbia", "md"),
    ("st. louis", "mo"), ("st louis", "mo"), ("saint louis", "mo"), ("clayton", "mo"),
    ("orlando", "fl"),
    ("charlotte", "nc"),
    ("san antonio", "tx"),
    ("portland", "or"), ("beaverton", "or"), ("hillsboro", "or"), ("vancouver", "wa"),
    ("sacramento", "ca"),
    ("pittsburgh", "pa"),
    # Austin, Las Vegas, Cincinnati, Kansas City, Columbus, Indianapolis, Cleveland
    ("austin", "tx"), ("round rock", "tx"),
    ("las vegas", "nv"), ("henderson", "nv"),
    ("cincinnati", "oh"),
    ("kansas city", "mo"), ("kansas city", "ks"), ("overland park", "ks"),
    ("columbus", "oh"), ("dublin", "oh"),
    ("indianapolis", "in"), ("carmel", "in"),
    ("cleveland", "oh"),
    # San Jose is above; Nashville, Virginia Beach, Providence, Jacksonville, Milwaukee
    ("nashville", "tn"), ("franklin", "tn"),
    ("virginia beach", "va"), ("norfolk", "va"),
    ("providence", "ri"),
    ("jacksonville", "fl"),
    ("milwaukee", "wi"),
    # Raleigh-Durham, Oklahoma City, Memphis, Richmond, Louisville, Salt Lake City
    ("raleigh", "nc"), ("durham", "nc"), ("cary", "nc"), ("chapel hill", "nc"),
    ("research triangle park", "nc"),
    ("oklahoma city", "ok"),
    ("memphis", "tn"),
    ("richmond", "va"),
    ("louisville", "ky"),
    ("salt lake city", "ut"), ("lehi", "ut"), ("provo", "ut"), ("draper", "ut"),
    # Hartford, Buffalo, Birmingham, Rochester, Grand Rapids, Tucson, Honolulu and others
    ("hartford", "ct"),
    ("buffalo", "ny"),
    ("birmingham", "al"),
    ("rochester", "ny"),
    ("grand rapids", "mi"),
    ("tucson", "az"),
    ("honolulu", "hi"),
    ("tulsa", "ok"),
    ("omaha", "ne"),
    ("albuquerque", "nm"),
    ("boise", "id"),
    ("madison", "wi"),
    ("des moines", "ia"),
)  # fmt: skip
_BY_CITY: dict[str, set[str]] = {}
for _city, _state in _METRO_CITIES:
    _BY_CITY.setdefault(_city, set()).add(_state)

_STATE_NAMES = {
    "alabama": "al", "arizona": "az", "california": "ca", "colorado": "co",
    "connecticut": "ct", "delaware": "de", "district of columbia": "dc", "florida": "fl",
    "georgia": "ga", "hawaii": "hi", "idaho": "id", "illinois": "il", "indiana": "in",
    "iowa": "ia", "kansas": "ks", "kentucky": "ky", "maryland": "md", "massachusetts": "ma",
    "michigan": "mi", "minnesota": "mn", "missouri": "mo", "nebraska": "ne", "nevada": "nv",
    "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa", "rhode island": "ri",
    "tennessee": "tn", "texas": "tx", "utah": "ut", "virginia": "va", "washington": "wa",
    "wisconsin": "wi",
}  # fmt: skip
_SPLIT = re.compile(r"\s*(?:;|/|\||\bor\b|\band\b)\s*", re.I)
_SUFFIX = re.compile(r"\s*\b(?:metro(?:politan)?(?: area)?|area|hq|office|headquarters)\b\s*", re.I)


def _place_in_metro(place: str) -> bool:
    text = _SUFFIX.sub(" ", place.lower()).replace("(", " ").replace(")", " ")
    parts = [part.strip(" .-") for part in text.split(",") if part.strip(" .-")]
    if not parts:
        return False
    city = " ".join(parts[0].split())
    states = _BY_CITY.get(city)
    if not states:
        return False
    # The state the posting gives, by code or name, must be the city's.
    for part in parts[1:]:
        code = _STATE_NAMES.get(part, part)
        if len(code) == 2:
            return code in states
    return True


def in_major_metro(location: str | None) -> bool:
    """Whether a posting's location names a place in a major US metro area."""
    return any(_place_in_metro(place) for place in _SPLIT.split(location or "") if place.strip())

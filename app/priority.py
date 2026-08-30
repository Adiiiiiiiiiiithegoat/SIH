"""Priority score, and the plain-English sentence that justifies it.

    priority = belief * (population ** POPULATION_EXPONENT) * delay_minutes

The three weights came out of a parameter sweep and live in config.py. They are
not re-derived here -- this module only applies them.

The reason string is the point of the whole system. An operator scanning a
queue of 200 reports reads the sentence, not the number, so it must state the
consequence -- who loses what, and how far away help now is. It must never
restate the arithmetic.
"""

from app import config
from app.network import net


# --- score ----------------------------------------------------------------
def belief(state, confidence, n_reports=1, status="pending"):
    """How much we believe this road is actually blocked, in [0, 1].

    Corroboration has diminishing returns -- the second report of a blockage
    tells you far more than the sixth -- so it saturates rather than stacking.
    An operator confirming the report is worth more than any number of citizen
    reports, but still cannot push belief past certainty.

    ``n_reports=0`` is meaningful and distinct from 1: it is a HYPOTHETICAL
    blockage nobody has reported, which the offline sweep scores. The formula
    handles it directly -- 0.6 + 0.4 * (1 - 0.5**0) = 0.6 -- so a hypothetical
    is damped below a single real report rather than borrowing its
    corroboration. ``None`` still means "unspecified" and defaults to 1.
    """
    if state == "impassable" or state == "damaged":
        base = float(confidence or 0.0)
    elif state == "unknown":
        base = config.UNKNOWN_BELIEF
    else:                                   # passable / not_damaged
        return 0.0

    n = 1 if n_reports is None else max(0, int(n_reports))
    base *= 0.6 + config.CORROBORATION_WEIGHT * (1 - 0.5 ** n)
    if status == "resolved":
        base *= config.CONFIRMATION_BOOST
    return min(1.0, base)


def delay_minutes(severed, added_km=0.0):
    """Minutes of extra travel to care caused by this blockage.

    A severance is not a long detour -- there is no route at any speed -- so it
    gets a flat penalty far above any real detour rather than an infinity that
    would make every severed report tie.
    """
    if severed:
        return float(config.DISCONNECT_PENALTY)
    return (float(added_km) / config.ASSUMED_SPEED_KMH) * 60.0


def score(state, confidence, population, severed, added_km=0.0,
          n_reports=1, status="pending"):
    """The contract's priority_score. None when nothing is at stake.

    Population is raised to POPULATION_EXPONENT so a settlement ten times larger
    ranks about 2.5x higher, not 10x -- cutting off 60 people is not a twentieth
    of the emergency of cutting off 1200.
    """
    b = belief(state, confidence, n_reports, status)
    d = delay_minutes(severed, added_km)
    if b <= 0 or d <= 0:
        return 0.0
    # A pocket the raster cannot measure is not an empty pocket. Scoring it as
    # zero people would bury a real severance; assume a small settlement and
    # let population_source tell the operator the figure is not measured.
    pop = config.ASSUMED_POCKET_POPULATION if not population else float(population)
    return round(b * (pop ** config.POPULATION_EXPONENT) * d, 1)


# --- reason ---------------------------------------------------------------
def _a(n):
    """"an 8-node pocket", not "a 8-node pocket".

    Read aloud, only a leading 8 ("eight", "eighty", "eight hundred") and the
    bare 11 and 18 start with a vowel sound. 110 is "a hundred and ten".
    """
    return "an" if str(n)[0] == "8" or n in (11, 18) else "a"


def _people(pop, source):
    if not pop:
        return "population unknown here"
    n = f"{round(pop):,} people"
    return n if source == "census" else f"about {n}"


def _corroboration(n_reports, status):
    if status == "resolved":
        return "confirmed on the ground"
    if n_reports and n_reports > 1:
        return f"{n_reports} corroborating reports"
    return None


def reason(report, pocket=None, detour=None):
    """One readable sentence about what this report costs, not what it scores.

    Shape, by situation:

        "Severs a 24-node pocket near Mukka from Health Subcenter;
         about 682 people, prior access 1.58 km"
        "Adds 4.16 km to Kadike's route to care; 3 corroborating reports"
        "Unassessed road on the sole approach to Chitrapu; needs inspection"
    """
    state = report.get("state")
    asset = report.get("asset_type", "road")
    extra = _corroboration(report.get("n_reports"), report.get("status"))

    if asset == "building":
        return _building_reason(report, state, extra)

    if state == "passable":
        parts = ["Road reported clear; no access impact"]
        return _join(parts, extra)

    if pocket:
        near = pocket.get("nearest_place")
        where = f" near {near}" if near else ""
        lost = pocket.get("lost_facility")
        from_ = f" from {lost}" if lost else " from all health facilities"
        bits = [_people(pocket.get("population"), pocket.get("population_source"))]
        if pocket.get("prior_access_km") is not None:
            bits.append(f"prior access {pocket['prior_access_km']:.2f} km")
        if extra:
            bits.append(extra)
        verb = "Severs" if state == "impassable" else "Would sever"
        return (f"{verb} {_a(pocket['n_nodes'])} {pocket['n_nodes']}-node "
                f"pocket{where}{from_}; " + ", ".join(bits))

    if detour:
        near = detour.get("nearest_place")
        whose = f" to {near}'s route to care" if near else " to the route to care"
        verb = "Adds" if state == "impassable" else "Would add"
        parts = [f"{verb} {detour['added_km']:.2f} km{whose}"]
        pop = detour.get("population")
        if pop:
            parts[0] += f" for {_people(pop, detour.get('population_source'))}"
        return _join(parts, extra)

    # A road report that never landed on a span was not assessed against the
    # network at all. Saying "alternative routes remain" here would assert a
    # conclusion we never computed -- the honest answer is that an operator has
    # to place it before anything can be said about consequence.
    if "edge_id" in report and not report["edge_id"]:
        near = _nearest_label(report)
        where = f" near {near}" if near else ""
        return _join([f"Not matched to a road span{where}; "
                      "place it on the map to assess access impact"], extra)

    # No pocket and no detour: the network absorbs this blockage.
    if state == "unknown":
        near = _nearest_label(report)
        where = f" near {near}" if near else ""
        return _join([f"Unassessed road{where}; needs inspection"], extra)
    near = _nearest_label(report)
    where = f" near {near}" if near else ""
    return _join([f"Blocked road{where}, but alternative routes remain"], extra)


def _building_reason(report, state, extra):
    near = _nearest_label(report)
    where = f" near {near}" if near else ""
    if state == "damaged":
        return _join([f"Damaged structure{where}; assess for occupants and debris"], extra)
    if state == "not_damaged":
        return _join([f"Structure{where} reported intact"], extra)
    return _join([f"Unassessed structure{where}; needs inspection"], extra)


def _nearest_label(report):
    lat, lon = report.get("lat"), report.get("lon")
    if lat is None or lon is None:
        return None
    name, _ = net().nearest_place(lat, lon)
    return name


def _join(parts, extra):
    return "; ".join(parts + ([extra] if extra else []))

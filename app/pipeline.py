"""The recompute: reports -> blocked edges -> severance -> priority -> queue order.

Every write path goes through `recompute()`. A citizen photo, an operator's map
click and an edge override are the same thing by the time they get here, which
is the point -- there is one ranking, computed one way.

Cost is dominated by the severance query itself (~200 ms). The hypothetical
"what would happen if this unassessed road were also blocked" pass is memoised
per edge, so a queue of 200 reports on 40 distinct roads costs 40 lookups, not
200.
"""
import threading

from app import priority, severance, store

_lock = threading.Lock()
_state = {"pockets": [], "detours": [], "blocked": [], "elapsed_ms": 0.0}


def current():
    """The last computed severance picture. Cheap -- no graph work."""
    return _state


def blocked_edges():
    """Edges a live report says are impassable.

    Rejected reports do not block: an operator rejecting a report is saying the
    road is not blocked. Resolved ones still do -- resolved means the report was
    confirmed, not that the road was cleared.
    """
    rows = store.conn().execute(
        "SELECT DISTINCT edge_id FROM reports "
        "WHERE edge_id IS NOT NULL AND asset_type = 'road' "
        "AND state = 'impassable' AND status != 'rejected'")
    return sorted(r["edge_id"] for r in rows)


def _corroboration_counts():
    """edge_id -> how many live reports name it. Drives n_reports on all of them."""
    rows = store.conn().execute(
        "SELECT edge_id, COUNT(*) AS c FROM reports "
        "WHERE edge_id IS NOT NULL AND status != 'rejected' GROUP BY edge_id")
    return {r["edge_id"]: r["c"] for r in rows}


def _attribute(result):
    """edge_id -> the pocket or detour region it is responsible for.

    A pocket names the blocked edges that straddle it, so severance attribution
    is exact. A detour region is caused by the blockages collectively; it is
    attributed to a blocked edge when one of that edge's own endpoints sits in
    the affected node set, which is exact for a single blockage and a fair
    reading when several interact.
    """
    by_edge = {}
    for p in result["pockets"]:
        for eid in p["severing_edges"]:
            by_edge.setdefault(eid, ("pocket", p))
    for d in result["detours"]:
        nodes = set(d["nodes"])
        for eid in result["blocked"]:
            if eid in by_edge:
                continue
            u, v = (int(x) for x in eid.split("-"))
            if u in nodes or v in nodes:
                by_edge[eid] = ("detour", d)
    return by_edge


def recompute():
    """Rescore and re-rank every report against the current blockages.

    Returns the severance result so a caller can report timing.
    """
    with _lock:
        blocked = blocked_edges()
        result = severance.analyse(blocked)
        by_edge = _attribute(result)
        counts = _corroboration_counts()
        frozen = tuple(blocked)

        for report in store.all_reports(full=True):
            eid = report["edge_id"]
            n_reports = counts.get(eid, 1) if eid else 1
            report["n_reports"] = n_reports

            pocket = detour = None
            if report["asset_type"] == "road" and eid:
                kind, region = by_edge.get(eid, (None, None))
                if kind == "pocket":
                    pocket = region
                elif kind == "detour":
                    detour = region
                elif report["state"] == "unknown" and report["status"] != "rejected":
                    # Not blocked today, so it is in no pocket. Ask the counter-
                    # factual: if this unassessed road turned out to be blocked,
                    # what would it cost? That is what makes an uninspected road
                    # on a sole approach rank above one on a redundant grid.
                    would = severance.hypothetical(eid, frozen)
                    pocket = would[0] if would else None

            severed = pocket is not None
            added_km = detour["added_km"] if detour else 0.0
            pop = (pocket or detour or {}).get("population")

            score = None
            if report["status"] == "rejected":
                reason = "Rejected by an operator; not counted in the network picture"
            else:
                score = priority.score(
                    report["state"], report["confidence"], pop, severed, added_km,
                    n_reports, report["status"])
                reason = priority.reason(report, pocket, detour)

            store.update(report["id"], n_reports=n_reports,
                         priority_score=score, priority_reason=reason)

        _state.update(pockets=result["pockets"], detours=result["detours"],
                      blocked=result["blocked"], elapsed_ms=result["elapsed_ms"])
        return result


def roads():
    """Every edge with its current state and geometry, for the map layer.

    State is what the live reports say: impassable or unknown where a report
    names the edge, passable everywhere else. That is a claim about reports, not
    an inspection -- an unreported road is not a verified-clear road.
    """
    from app.network import net

    n = net()
    reported = {}
    for r in store.conn().execute(
            "SELECT edge_id, state, MAX(priority_score) AS s FROM reports "
            "WHERE edge_id IS NOT NULL AND asset_type = 'road' "
            "AND status != 'rejected' GROUP BY edge_id, state"):
        # impassable beats unknown beats passable when a road has mixed reports
        rank = {"impassable": 3, "unknown": 2, "passable": 1}
        if rank.get(r["state"], 0) > rank.get(reported.get(r["edge_id"], ""), 0):
            reported[r["edge_id"]] = r["state"]

    return [{"edge_id": eid,
             "coordinates": n.span_coords(eid),
             "state": reported.get(eid, "passable"),
             "name": n.span_name(eid),
             "length_m": round(n.span_length(eid), 1)}
            for eid in n.spans]

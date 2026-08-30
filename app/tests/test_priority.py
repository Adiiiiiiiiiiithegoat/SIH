"""Priority scoring against values worked out by hand from the locked weights.

The constants came out of a parameter sweep and are not to be re-derived, so
these are the arithmetic written out longhand -- if someone "improves" the
formula, these fail.

    priority = belief * (population ** 0.4) * delay_minutes

    belief:  impassable -> confidence, unknown -> 0.5, passable -> 0
             * (0.6 + 0.4 * (1 - 0.5 ** n_reports))
             * 1.25 if resolved, capped at 1.0
    delay:   severed -> 300 minutes, else (added_km / 35) * 60
"""
import pytest

from app import config, priority


# --- belief ---------------------------------------------------------------
def test_impassable_belief_is_confidence_damped_by_a_single_report():
    # 0.80 * (0.6 + 0.4 * (1 - 0.5**1)) = 0.80 * 0.80 = 0.64
    assert priority.belief("impassable", 0.80, 1, "pending") == pytest.approx(0.64)


def test_unknown_belief_is_the_configured_half():
    # 0.5 * 0.80 = 0.40 -- confidence is ignored, the state is what is unknown
    assert priority.belief("unknown", 0.99, 1, "pending") == pytest.approx(0.40)
    assert priority.belief("unknown", 0.01, 1, "pending") == pytest.approx(0.40)


def test_passable_belief_is_zero():
    assert priority.belief("passable", 1.0, 9, "resolved") == 0.0


def test_corroboration_saturates_rather_than_stacking():
    # 0.6 + 0.4 * (1 - 0.5**n): 0.80, 0.90, 0.95, 0.975 ...
    b = lambda n: priority.belief("impassable", 1.0, n, "pending")
    assert b(1) == pytest.approx(0.80)
    assert b(2) == pytest.approx(0.90)
    assert b(3) == pytest.approx(0.95)
    assert b(4) == pytest.approx(0.975)
    assert b(50) < 1.0, "corroboration must never reach certainty on its own"


def test_operator_confirmation_boosts_then_caps_at_one():
    # 0.50 * 0.95 * 1.25 = 0.59375
    assert priority.belief("impassable", 0.50, 3, "resolved") == pytest.approx(0.59375)
    # 0.90 * 0.95 * 1.25 = 1.06875 -> capped
    assert priority.belief("impassable", 0.90, 3, "resolved") == 1.0


def test_damaged_building_uses_confidence_like_impassable():
    assert priority.belief("damaged", 0.80, 1, "pending") == pytest.approx(0.64)
    assert priority.belief("not_damaged", 0.80, 1, "pending") == 0.0


# --- delay ----------------------------------------------------------------
def test_severed_delay_is_the_flat_disconnect_penalty():
    assert priority.delay_minutes(True) == 300.0
    assert priority.delay_minutes(True, added_km=999) == 300.0


def test_detour_delay_is_distance_over_the_assumed_speed():
    # (4.16 km / 35 km/h) * 60 = 7.131428... minutes
    assert priority.delay_minutes(False, 4.16) == pytest.approx(7.1314286)
    assert priority.delay_minutes(False, 35.0) == pytest.approx(60.0)
    assert priority.delay_minutes(False, 0.0) == 0.0


# --- score ----------------------------------------------------------------
def test_severed_pocket_score_matches_the_longhand_arithmetic():
    # belief 0.80 * (0.6 + 0.4*0.5) = 0.64
    # 682 people ** 0.4 = 13.5992122
    # delay 300 minutes
    # 0.64 * 13.5992122 * 300 = 2611.0 (1 dp)
    assert 682 ** 0.4 == pytest.approx(13.5992122, abs=1e-6)
    assert priority.score("impassable", 0.80, 682, True) == pytest.approx(2611.0, abs=0.1)


def test_detour_score_matches_the_longhand_arithmetic():
    # belief 0.64, 253 ** 0.4 = 9.1463583, delay (4.16/35)*60 = 7.1314286
    # 0.64 * 9.1463583 * 7.1314286 = 41.7 (1 dp)
    assert 253 ** 0.4 == pytest.approx(9.1463583, abs=1e-6)
    assert priority.score("impassable", 0.80, 253, False, 4.16) == pytest.approx(41.7, abs=0.1)


def test_a_clear_road_scores_zero_however_confident():
    assert priority.score("passable", 1.0, 5000, False, 0) == 0.0


def test_a_blockage_that_costs_nothing_scores_zero():
    """Believed blocked, but the network routes around it for free."""
    assert priority.score("impassable", 1.0, 5000, False, 0.0) == 0.0


def test_population_is_sublinear_not_proportional():
    """The exponent is the whole point: 10x the people is ~2.5x the priority,
    because cutting off 60 people is not a twentieth of the emergency."""
    small = priority.score("impassable", 1.0, 100, True)
    large = priority.score("impassable", 1.0, 1000, True)
    assert large / small == pytest.approx(10 ** config.POPULATION_EXPONENT, abs=0.01)


def test_unmeasured_population_is_not_scored_as_empty():
    """A pocket the raster cannot measure must not sink to zero priority -- an
    unmeasured pocket is not an empty one."""
    assert priority.score("impassable", 0.9, None, True) > 0
    assert priority.score("impassable", 0.9, None, True) == \
        priority.score("impassable", 0.9, config.ASSUMED_POCKET_POPULATION, True)


def test_severance_outranks_a_detour_over_the_same_people():
    """DISCONNECT_PENALTY is 300 minutes -- far above any real detour -- so for
    a given population, no route at all always outranks a longer route.

    Note the formula does NOT make severance beat every detour everywhere: a
    20 km detour for 5000 people outscores severing 50. That is the sweep's
    weighting, deliberately left alone.
    """
    for pop in (50, 682, 5000):
        severed = priority.score("impassable", 0.6, pop, True)
        detour = priority.score("impassable", 1.0, pop, False, added_km=20.0)
        assert severed > detour


# --- reason ---------------------------------------------------------------
POCKET = {"n_nodes": 24, "nearest_place": "Mukka", "lost_facility": "Health Subcenter",
          "population": 681.6, "population_source": "raster", "prior_access_km": 1.58}
DETOUR = {"added_km": 4.16, "nearest_place": "Kadike", "population": 253.0,
          "population_source": "raster"}


def test_severance_reason_states_the_consequence():
    text = priority.reason(
        {"state": "impassable", "asset_type": "road", "n_reports": 1,
         "status": "pending"}, pocket=POCKET)
    assert text == ("Severs a 24-node pocket near Mukka from Health Subcenter; "
                    "about 682 people, prior access 1.58 km")


def test_detour_reason_names_the_added_distance_and_who_pays_it():
    text = priority.reason(
        {"state": "impassable", "asset_type": "road", "n_reports": 3,
         "status": "pending"}, detour=DETOUR)
    assert text == ("Adds 4.16 km to Kadike's route to care for about 253 people; "
                    "3 corroborating reports")


def test_unassessed_road_on_a_sole_approach_asks_for_inspection():
    text = priority.reason(
        {"state": "unknown", "asset_type": "road", "n_reports": 1,
         "status": "pending"}, pocket=dict(POCKET, n_nodes=9,
                                           nearest_place="Chitrapu"))
    assert text.startswith("Would sever a 9-node pocket near Chitrapu")


def test_reason_never_restates_the_score():
    """It must read as a consequence, not as arithmetic. No 'priority', no
    'score', no formula terms."""
    for kwargs in ({"pocket": POCKET}, {"detour": DETOUR}, {}):
        text = priority.reason({"state": "impassable", "asset_type": "road",
                                "n_reports": 2, "status": "pending",
                                "lat": 13.02, "lon": 74.79}, **kwargs).lower()
        for banned in ("priority", "score", "belief", "delay", "minutes",
                       "confidence", "weight", "exponent"):
            assert banned not in text, f"{banned!r} leaked into: {text}"


def test_census_and_raster_figures_are_worded_differently():
    """A census count is enumerated; a raster figure is modelled. The sentence
    must not present them as the same measurement."""
    census = priority.reason({"state": "impassable", "asset_type": "road",
                              "n_reports": 1, "status": "pending"},
                             pocket=dict(POCKET, population=1326,
                                         population_source="census"))
    raster = priority.reason({"state": "impassable", "asset_type": "road",
                              "n_reports": 1, "status": "pending"}, pocket=POCKET)
    assert "1,326 people" in census and "about" not in census
    assert "about 682 people" in raster


def test_unmeasured_population_is_said_to_be_unknown_not_zero():
    text = priority.reason({"state": "impassable", "asset_type": "road",
                            "n_reports": 1, "status": "pending"},
                           pocket=dict(POCKET, population=None,
                                       population_source="missing"))
    assert "population unknown here" in text
    assert "0 people" not in text


def test_building_reports_get_their_own_wording(network):
    text = priority.reason({"state": "damaged", "asset_type": "building",
                            "n_reports": 1, "status": "pending",
                            "lat": 13.0224, "lon": 74.7900})
    assert text.startswith("Damaged structure near ")
    assert "pocket" not in text and "route to care" not in text

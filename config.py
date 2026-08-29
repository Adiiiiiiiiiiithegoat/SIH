"""Locked scoring constants. Single source of truth — don't scatter these."""

# Priority = belief * (population ** POPULATION_EXPONENT) * delay_minutes

POPULATION_EXPONENT = 0.4
DISCONNECT_PENALTY = 300      # minutes
UNKNOWN_BELIEF = 0.5
CORROBORATION_WEIGHT = 0.4
CONFIRMATION_BOOST = 1.25
ASSUMED_SPEED_KMH = 35

# belief:
#   impassable -> model confidence
#   unknown    -> 0.5
#   passable   -> 0
#   then * (0.6 + 0.4 * (1 - 0.5 ** n_reports))
#   then * 1.25 if operator-confirmed, capped at 1.0
#
# delay_minutes:
#   disconnected from all care -> 300
#   otherwise -> (extra_km / 35) * 60

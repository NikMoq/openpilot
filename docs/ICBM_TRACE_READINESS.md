# Observed PQ ACC input readiness

2026-10-28, read-only immutable SQLite decoding by independent reviewer.
DBC: opendbc_repo/opendbc/dbc/vw_pq.dbc, ACC_System 0x368, ACC_GRA_Anzeige 0x56A.

| Observation | icbm trace | button trace |
|---|---:|---:|
| ACS_Sta_ADR=1, ACS_FreigSollB=1 | 35239 | 6583 |
| ACS_Sta_ADR=2, ACS_FreigSollB=0 | 11346 | 29489 |
| ACS_Sta_ADR=1, ACS_FreigSollB=0 | 16 | 3 |
| ACS_Sta_ADR=0, ACS_FreigSollB=0 | 0 | 50 |
| ACA_kmh_mph=0 / =1 | 23304 / 0 | 18061 / 0 |
| GRA median/max interval ms | 20 / 40.147 | 20 / 39.611 |
| ACC_System median/max interval ms | 20 / 100.005 | 20 / 40 |
| ACC display median/max interval ms | 40 / 45.029 | 40 / 80 |

CONFIRMED: active readiness gate (1,1) is reachable in both stored traces; observed message gaps are below the 120ms runtime threshold. This does not measure current runtime latency or guarantee no packet loss.

Inputs outside Git:
- Downloads/Telegram Desktop/icbm SHA256 79b7dde4dfca7cbc7665748d53ef5143684c35acb11af251e57357c793f98bd0
- Downloads/Telegram Desktop/нажатия_ктопок_увеличения_скорости SHA256 eb92f33b766b078814b31b01311799917e68886059e336cf5ab8eb446544bd3d
- Existing primary_verified.json SHA256 7fb2bdea6350fea34f618ad9fec8fad5d563caecc4670a4960577221feeb35ff

All counted frames were RX/channel1 in the recording. Recording channel numbering is not evidence of Panda bus numbering. Vehicle identity follows trace provenance; VIN was not inspected. These observations do not prove acceptance of newly generated presses or mph behavior.

## Unit evidence boundary

The retained PQ DBC declares ACA_V_Wunsch in km/h independently of the
ACA_kmh_mph display flag; CarState also always converts the raw value using
KPH_TO_MS. Both recorded traces contain only flag0. Thus treating the raw byte
as mph when flag1 is set, or enabling a guessed 5mph coarse model, is unproven.
Current selector rejects mph and mismatched/unknown units. Full unit parity
requires a trace linking raw setpoint, actual displayed units and each manual
button response in imperial mode. Metric Caddy evidence cannot establish it.

Metric DOWN evidence starts only on decade-aligned setpoints; off-decade
responses remain unverified. The selector therefore uses fine SET from
off-decade values and reserves coarse DOWN for decade-aligned values.

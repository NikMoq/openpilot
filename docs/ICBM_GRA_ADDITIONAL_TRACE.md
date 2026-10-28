# Additional GRA trace, 2026-10-28

User attribution: PQ35, km/h. Model/year not separately stated.
Input: Downloads/Telegram Desktop/работа gra
SHA-256: 8729821518e8323f3a17b66c1c958660088d2d1a9060520b88cb4e15f2fbb541
Original opened SQLite mode=ro&immutable=1 and hash checked before/after.

503660 frames over214.605026s, all RX/channel1. Physical speed0 in21462 wheel-speed samples. ACC_System readiness=(2,0)10681 frames and(0,0)50, never active(1,1). All5366 unit flags are km/h. Valid raw setpoints are30..210 by10; no nondecade value distinguishes strict-next-decade from a fixed10 step.

80 manual episodes: UP31, DOWN23, RES8, SET18. UP has30 first responses+10 plus sentinel255->30; DOWN20 first responses-10,3 nochange. First coarse responses63..91.969ms. Six held coarse episodes repeat without release (UP2,DOWN4), median480ms between steps. Long flags begin after23 source frames. Example UP68frames/1.340s:110->120->130->140; DOWN90frames/1.780s:140->130->120->110->100.

No fine response is seen with inactive stationary ACC, including long RES/SET holds. This does not contradict earlier moving active fine evidence. Largest relevant signal interarrival44ms is below120ms freshness threshold. Single logger channel1 is not a Panda bus/TX-return mapping.

Independent report outside Git: workspace/results/icbm_gra_20261028/analysis.json
SHA-256: 7b2c7bec7da5865ef639f1c5cb25e6fc1337b80485dc864cbf3bcc446deb1030
Script: same directory/analyze_gra.py. Original dumps remain outside Git.

## Newly explicit source parity gap

Original shared ICBM does not require motion, and Hyundai backend sends fine RES/SET with cadence only. When ACC is enabled and standstill with planner.shouldStop=true, global resume=false; automatic target adjustment is logically reachable. Current PQ firmware requires vehicle_moving for fine SET/RES admission and forwarding. Therefore active ACC+standstill+fine target adjustment is a source-level behavior difference. Coarse actions and legacy standstill resume are separate paths.

Current data does not justify removing that guard. A narrow query of earlier originals with fresh radar/speed<=120ms found0 active fine frames in icbm SHA79b7, and57 active fine frames in speed_buttons SHAeb92 with minimum speed24km/h, none<=1km/h. The stopped RES at119469144us has inactive radar(2,0) and no target change; active SET295668205us is40.83km/h; active RES615144144..616744144us is26.46..28.27km/h.

This is an unresolved equivalence requirement, not completion. Need evidence of native semantics for active stopped ACC and fine commands before changing motion gating. The supplied traces provide no PQ46 or mph evidence. No firmware or device configuration changed because of this trace.

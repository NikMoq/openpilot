@0xa2a813faf07411b1;
# Locally declared wire contract of installed mapd offline geometry tiles.
# Layout verified against mapd v1.12.0; speed fields are padding, never read.
struct Coordinates { latitude @0 :Float64; longitude @1 :Float64; }
struct Way {
  name @0 :Text; ref @1 :Text; maxSpeed @2 :Float64;
  minLat @3 :Float64; minLon @4 :Float64; maxLat @5 :Float64; maxLon @6 :Float64;
  nodes @7 :List(Coordinates); lanes @8 :UInt8; advisorySpeed @9 :Float64;
  hazard @10 :Text; oneWay @11 :Bool;
  maxSpeedForward @12 :Float64; maxSpeedBackward @13 :Float64;
}
struct Offline {
  minLat @0 :Float64; minLon @1 :Float64; maxLat @2 :Float64; maxLon @3 :Float64;
  ways @4 :List(Way); overlap @5 :Float64;
}

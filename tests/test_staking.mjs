import assert from "node:assert/strict";
import {createRequire} from "node:module";
const require = createRequire(import.meta.url);
const S = require("../site/staking.js");

const base = S.normalise({bankroll:500,system:"kelly",kelly_fraction:.15,
  max_stake_pct:.10,max_slate_exposure_pct:.35,min_stake:1,round_to:.5});
const row = {tier:"GOOD",price_american:-110,action_edge:.04,confidence:.65,
  projection_samples:8,season_maturity:.65,market_reliability:1,push_prob:0};
assert.ok(S.suggestedStake(row,base,500,.85) > 0,"qualified props receive a conservative Kelly suggestion");
assert.equal(S.suggestedStake({...row,tier:"PASS"},base,500,.85),0,"PASS rows never receive a stake");
assert.equal(S.suggestedStake({...row,held:true},base,500,.85),0,"held rows never receive a stake");
assert.equal(S.suggestedStake(row,{...base,system:"flat",flat_stake:100},500,.85),50,"per-play cap applies to flat stakes");
assert.equal(S.suggestedStake(row,{...base,system:"percent",bankroll_pct:.02},500,.85),10,"percent staking follows bankroll");

const plan = S.plan(Array.from({length:4},()=>row),
  {...base,system:"flat",flat_stake:50,max_stake_pct:1,max_slate_exposure_pct:.35},100,.85);
assert.ok(plan.reduce((sum,value)=>sum+value,0) <= 35,"slate exposure cap is enforced");
assert.equal(S.normalise({kelly_fraction:4,max_stake_pct:-1,round_to:0}).kelly_fraction,1);
console.log("props staking tests passed");

import assert from "node:assert/strict";
import fs from "node:fs";
const app=fs.readFileSync(new URL("../site/app.js",import.meta.url),"utf8");
const index=fs.readFileSync(new URL("../site/index.html",import.meta.url),"utf8");
const parlays=fs.readFileSync(new URL("../pipeline/parlays.py",import.meta.url),"utf8");
const start=app.indexOf("function renderStatus()"),end=app.indexOf("function renderMetrics()",start);
assert.ok(start>=0&&end>start);
const source=app.slice(start,end);
function status(meta){
  const nodes=new Map();
  const $=id=>{
    if(!nodes.has(id))nodes.set(id,{textContent:"",className:"",querySelector(){return this;}});
    return nodes.get(id);
  };
  new Function("state","$",source+";renderStatus();")({
    schemaReady:true,board:[],meta:{generated_at:new Date().toISOString(),counts:{},source_by_sport:{NFL:{projections:12}},...meta}
  },$);
  return {banner:$("#statusBanner").textContent,feed:$("#feedState").textContent};
}
const keyless=status({odds_mode:"keyless"});
assert.equal(keyless.feed,"KEYLESS");
assert.match(keyless.banner,/API-key requests are disabled/);
assert.match(keyless.banner,/No verified keyless prop-price source/);
assert.doesNotMatch(keyless.banner,/closer to kickoff/);
assert.match(status({odds_mode:"keyless",source_by_sport:{NFL:{projections:0}}}).banner,/statistics are not ready/);
assert.equal(status({}).feed,"NO PRICES");
assert.equal(status({odds_mode:"keyless",generated_at:"2000-01-01T00:00:00Z"}).feed,"STALE");
const retained=status({price_source_status:"cached",source_by_sport:{NFL:{projections:12,eligible_priced_quotes:3}}});
assert.equal(retained.feed,"RECENT");
assert.match(retained.banner,/recently observed/);
assert.match(retained.banner,/original observation times/);
assert.doesNotMatch(app,/no wager can qualify without a complete live price/i);
assert.match(index,/one-sided offer may qualify only at LEAN/i);
assert.doesNotMatch(index,/requires a complete current market/i);
assert.match(parlays,/may qualify on the straight-bet board only at LEAN/i);
assert.doesNotMatch(parlays,/remain ineligible for the stricter straight-bet board/i);
console.log("Keyless feed status checks passed.");

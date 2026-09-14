import assert from "node:assert/strict";
import fs from "node:fs";
const app=fs.readFileSync(new URL("../site/app.js",import.meta.url),"utf8");
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
console.log("Keyless feed status checks passed.");

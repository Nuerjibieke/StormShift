// Derive color-only overrides from the existing sheets; no layout or source CSS is rewritten.
import fs from "node:fs";
import path from "node:path";
import postcss from "../frontend/node_modules/postcss/lib/postcss.mjs";
const root=path.resolve(import.meta.dirname,"../frontend/src");
const sheets=["styles.css","contrastive.css","trusted-state.css","falsification.css","branchPreview.css",
  "guidedStory.css","evidenceReceipt.css","previewSelectionReceipt.css","replayNavigation.css","mapFirst.css","intentClarification.css"];
function ink(r,g,b) {
  if(r>g*1.22 && r>b*1.1)return [173,55,70];
  if(r>100 && g>r*.62 && b<g*.8)return [143,93,22];
  if(b>g*1.12 && r>g*1.1)return [112,73,168];
  if(b>r*1.15 && b>=g)return [39,103,148];
  return [37,103,95];
}
function transform(value,prop) {
  const text=prop==="color" || prop==="fill" || prop==="stroke";
  return value.replace(/#[0-9a-fA-F]{6,8}\b|rgba?\([^)]*\)/g, token=>{
    let vals,alpha=1;
    if(token[0]==="#"){vals=[1,3,5].map(i=>parseInt(token.slice(i,i+2),16));if(token.length===9)alpha=parseInt(token.slice(7,9),16)/255;}
    else {vals=token.match(/[\d.]+/g)?.map(Number);if(!vals||vals.length<3)return token;alpha=vals[3]??1;}
    const [r,g,b]=vals, max=Math.max(r,g,b), min=Math.min(r,g,b);
    if(prop.includes("shadow"))return "rgba(37,70,82,0.10)";
    if(text) {
      if(max<100)return token;
      const rgb=max-min<42?[66,91,102]:ink(r,g,b);
      return `rgba(${rgb.join(",")},${Math.max(alpha,.8)})`;
    }
    if(prop.startsWith("background")) {
      if(max<105)return `rgba(249,252,252,${Math.max(alpha,.8)})`;
      return token;
    }
    if(prop.includes("border")||prop.includes("outline")) {
      const rgb=max-min<50?[160,183,189]:ink(r,g,b);
      return `rgba(${rgb.join(",")},${Math.min(.48,Math.max(alpha,.18))})`;
    }
    return token;
  });
}
let output="/* Generated color-only daylight palette. Rebuild: node ops/build-daylight-theme.mjs */\n";
for(const file of sheets) {
  const ast=postcss.parse(fs.readFileSync(path.join(root,file),"utf8"));
  ast.walkComments(n=>n.remove());
  ast.walkDecls(d=>{
    if(d.prop.startsWith("--")||!/#|rgba?\(/.test(d.value)||!/^(color|background|border|outline|box-shadow|text-shadow|fill|stroke)/.test(d.prop)){d.remove();return;}
    const next=transform(d.value,d.prop);if(next===d.value)d.remove();else d.value=next;
  });
  ast.walkRules(r=>{if(!r.nodes?.length)r.remove()});
  ast.walkAtRules(r=>{if(!r.nodes?.length || r.name.includes("keyframes"))r.remove()});
  output+=ast.toString()+"\n";
}
fs.writeFileSync(path.join(root,"daylight.tokens.css"),output);
console.log("Generated daylight color overrides");

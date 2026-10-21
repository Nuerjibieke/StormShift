import { expect, it } from "vitest";
import { SceneFrameMeter } from "./sceneFrameMeter";
it("measures scene intervals, not a startup requestAnimationFrame count",()=>{
 const meter=new SceneFrameMeter(); let report;
 for(let i=0;i<=180;i++) report=meter.push(i*1000/60,"1920x1080:20:executing",true)??report;
 expect(report?.fps).toBeCloseTo(60); expect(report?.frames).toBe(180); expect(report?.elapsedMs).toBe(3000);
});
it("restarts after viewport/phase/flight-set changes or background visibility",()=>{
 const meter=new SceneFrameMeter();
 meter.push(0,"one",true); meter.push(2999,"one",true);
 expect(meter.push(3000,"two",true)).toBeUndefined();
 expect(meter.push(5000,"two",true)).toBeUndefined();
 expect(meter.push(6000,"two",false)).toBeUndefined();
 expect(meter.push(9000,"two",true)).toBeUndefined();
 expect(meter.push(10000,"two",true)).toBeUndefined();
});
it("rejects duplicate and non-monotonic timestamps",()=>{
 const meter=new SceneFrameMeter();
 meter.push(0,"a",true); meter.push(0,"a",true);
 const report=meter.push(3000,"a",true); expect(report?.frames).toBe(1);
 expect(meter.push(2990,"a",true)).toBeUndefined();
 expect(meter.push(NaN,"a",true)).toBeUndefined();
});

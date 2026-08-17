import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { QuestionRecord, validateDocument } from "./import";

type Session = { token: string; role: "reviewer" | "admin"; name: string };
type Question = QuestionRecord & { source_id: string };
type Metrics = { total:number; reviewed:number; pending:number; assigned:number; passed:number; failed:number;
  by_reviewer:{reviewer:string;reviews:number}[]; over_time:{date:string;reviews:number}[] };

const saved = sessionStorage.getItem("review-session");

export default function App() {
  const [session, setSession] = useState<Session | null>(saved ? JSON.parse(saved) : null);
  const logout = () => { sessionStorage.removeItem("review-session"); setSession(null); };
  if (!session) return <Login onLogin={(value) => { sessionStorage.setItem("review-session", JSON.stringify(value)); setSession(value); }} />;
  return <><header><div><strong>Review Desk</strong><span>{session.name}</span></div><button className="secondary" onClick={logout}>Sign out</button></header>
    <main>{session.role === "reviewer" ? <Reviewer session={session} /> : <Admin session={session} />}</main></>;
}

function Login({ onLogin }: { onLogin:(session:Session)=>void }) {
  const [name,setName]=useState(""); const [password,setPassword]=useState(""); const [error,setError]=useState("");
  const submit=async(e:React.FormEvent)=>{e.preventDefault();setError("");try{onLogin(await api<Session>("/api/auth/login","",{method:"POST",body:JSON.stringify({password,reviewer_name:name})}));}catch(err){setError((err as Error).message)}};
  return <main className="login"><section className="card"><h1>Review Desk</h1><p className="muted">Secure question and answer quality review</p><form onSubmit={submit}>
    <label>Reviewer name<input value={name} onChange={e=>setName(e.target.value)} autoComplete="name" /></label>
    <label>Password<input value={password} onChange={e=>setPassword(e.target.value)} type="password" required /></label>
    {error&&<p className="error">{error}</p>}<button type="submit">Sign in</button></form></section></main>;
}

function Reviewer({session}:{session:Session}) {
  const [question,setQuestion]=useState<Question|null|undefined>(); const [notes,setNotes]=useState(""); const [fail,setFail]=useState(false); const [error,setError]=useState("");
  const claim=useCallback(async(exclude?:number)=>{setError("");setQuestion(undefined);try{setQuestion(await api<Question|null>(`/api/reviewer/claim${exclude?`?exclude_id=${exclude}`:""}`,session.token,{method:"POST"}));}catch(e){setError((e as Error).message)}},[session.token]);
  useEffect(()=>{claim()},[claim]);
  useEffect(()=>{if(!question)return;const timer=setInterval(()=>api(`/api/reviewer/renew/${question.id}`,session.token,{method:"POST"}).catch(()=>{}),5*60*1000);return()=>clearInterval(timer)},[question,session.token]);
  const submit=async(decision:"Pass"|"Fail")=>{if(decision==="Fail"&&!notes.trim())return setError("Failure notes are required.");try{await api("/api/reviewer/review",session.token,{method:"POST",body:JSON.stringify({question_id:question!.id,decision,notes})});setNotes("");setFail(false);claim()}catch(e){setError((e as Error).message)}};
  const skip=async()=>{const id=question!.id;await api(`/api/reviewer/skip/${id}`,session.token,{method:"POST"});claim(id)};
  return <><h1>Question review</h1><p className="muted">Read all sections, then record your decision.</p>{error&&<p className="error">{error}</p>}
    {question===undefined?<p>Loading…</p>:question===null?<section className="card"><h2>No questions are available</h2><button onClick={()=>claim()}>Check again</button></section>:<>
      <small>Source item ID: {question.source_id}</small><Content title="Instruction" text={question.instruction}/><Content title="Question" text={question.input.join("\n\n")}/><Content title="Output" text={question.output}/>
      {fail?<section className="card"><label>Failure notes *<textarea dir="auto" value={notes} onChange={e=>setNotes(e.target.value)} /></label><div className="actions"><button onClick={()=>submit("Fail")}>Submit Fail & Next</button><button className="secondary" onClick={()=>setFail(false)}>Cancel</button></div></section>
      :<div className="actions"><button onClick={()=>submit("Pass")}>Pass & Next</button><button className="danger" onClick={()=>setFail(true)}>Fail</button><button className="secondary" onClick={skip}>Skip</button></div>}</>}
  </>;
}

function Content({title,text}:{title:string;text:string}){return <section className="card"><h2>{title}</h2><div className="content" dir="auto">{text||"—"}</div></section>}

function Admin({session}:{session:Session}) {
  const [tab,setTab]=useState("upload");
  return <><h1>Administration</h1><nav>{["upload","analytics","reviews","export"].map(x=><button key={x} className={tab===x?"":"secondary"} onClick={()=>setTab(x)}>{x[0].toUpperCase()+x.slice(1)}</button>)}</nav>
    {tab==="upload"&&<Upload token={session.token}/>} {tab==="analytics"&&<Analytics token={session.token}/>} {tab==="reviews"&&<Reviews token={session.token}/>} {tab==="export"&&<Export token={session.token}/>}</>;
}

type Batch = {id:number; filename:string; uploaded_at:string; imported_count:number;
  skipped_count:number; status:"uploading"|"ready"; stored:number; reviewed:number};

function useBatches(token:string){
  const [batches,setBatches]=useState<Batch[]>([]);
  const reload=useCallback(async()=>{try{setBatches(await api<Batch[]>("/api/admin/batches",token))}catch{/* advisory only */}},[token]);
  useEffect(()=>{void reload()},[reload]);
  return {batches,reload};
}

function Upload({token}:{token:string}) {
  const [status,setStatus]=useState(""); const [errors,setErrors]=useState<{row:number;message:string}[]>([]);
  const {batches,reload}=useBatches(token);
  const upload=async(file:File)=>{
    setErrors([]); setStatus("Reading file…"); let started=false;
    try{
      const text=await file.text();
      const parsed=validateDocument(JSON.parse(text));
      setErrors(parsed.errors);
      if(!parsed.valid.length){setStatus(`No valid records found; ${parsed.errors.length} row(s) rejected. Nothing was uploaded.`);return}
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(text)))).map(x=>x.toString(16).padStart(2,"0")).join("");
      const start=await api<{batch_id:number}>("/api/admin/imports",token,{method:"POST",body:JSON.stringify({filename:file.name,file_hash:hash})});
      started=true; let sent=0;
      for(let i=0;i<parsed.valid.length;i+=200){
        const chunk=parsed.valid.slice(i,i+200);
        await api(`/api/admin/imports/${start.batch_id}/records`,token,{method:"POST",body:JSON.stringify({records:chunk,skipped_count:i===0?parsed.errors.length:0})});
        sent+=chunk.length; setStatus(`Uploading ${sent} / ${parsed.valid.length}…`);
      }
      await api(`/api/admin/imports/${start.batch_id}/finish`,token,{method:"POST"});
      setStatus(`Imported ${parsed.valid.length}; skipped ${parsed.errors.length}.`);
    }catch(e){
      setStatus(`${(e as Error).message}${started?" The import stopped partway, so this file's questions are not yet available. See Uploaded files below.":""}`);
    }finally{void reload()}
  };
  const complete=async(b:Batch)=>{
    if(!confirm(`Release the ${b.stored} question(s) already stored for ${b.filename}?\n\nRecords that never uploaded will not be added, and this file cannot be uploaded again afterwards.`))return;
    try{await api(`/api/admin/imports/${b.id}/finish`,token,{method:"POST"});setStatus(`${b.filename} completed.`)}
    catch(e){setStatus((e as Error).message)}finally{void reload()}
  };
  const remove=async(b:Batch)=>{
    if(!confirm(`Delete ${b.filename} and the ${b.stored} question(s) stored for it?\n\nThis cannot be undone. Afterwards the file can be uploaded again from scratch.`))return;
    try{const r=await api<{deleted_questions:number}>(`/api/admin/batches/${b.id}`,token,{method:"DELETE"});
      setStatus(`Deleted ${b.filename} and ${r.deleted_questions} question(s). You can upload the file again now.`)}
    catch(e){setStatus((e as Error).message)}finally{void reload()}
  };
  const stuck=batches.filter(b=>b.status!=="ready");
  return <>
    <section className="card"><h2>Upload questions</h2>
      <input type="file" accept="application/json,.json" onChange={e=>e.target.files?.[0]&&upload(e.target.files[0])}/>
      <p>{status}</p>
      {errors.length>0&&<p className="muted">{errors.length} row(s) rejected{errors.length>100?"; first 100 shown":""}.</p>}
      {errors.slice(0,100).map(e=><p className="error" key={e.row}>Row {e.row}: {e.message}</p>)}
    </section>
    <section className="card"><h2>Uploaded files</h2>
      <p className="muted">Only questions from a completed import are counted in analytics and offered to reviewers.</p>
      {stuck.length>0&&<p className="error">{stuck.length} import{stuck.length===1?"":"s"} did not finish. Their questions are stored but held back.</p>}
      {!batches.length&&<p className="muted">No files uploaded yet.</p>}
      {batches.map(b=><article key={b.id}>
        <div className="review-head">
          <span className={b.status==="ready"?"tag tag-pass":"tag tag-warn"}>{b.status==="ready"?"Complete":"Incomplete"}</span>
          <strong dir="auto">{b.filename}</strong>
          <span className="muted">{b.uploaded_at.slice(0,16).replace("T"," ")}</span>
        </div>
        <div className="muted">{b.stored} question{b.stored===1?"":"s"} stored
          {b.skipped_count?`, ${b.skipped_count} row(s) rejected`:""}
          {b.reviewed?`, ${b.reviewed} already reviewed`:""}</div>
        <div className="row">
          {b.status!=="ready"&&<button onClick={()=>complete(b)}>Complete import</button>}
          <button className="danger" disabled={b.reviewed>0} onClick={()=>remove(b)}>Delete upload</button>
        </div>
        {b.reviewed>0&&<p className="muted">This upload cannot be deleted while {b.reviewed} of its question{b.reviewed===1?" carries a review":"s carry reviews"}. Reset {b.reviewed===1?"it":"them"} under Reviews first.</p>}
      </article>)}
    </section>
  </>;
}

// "total" counts only questions a reviewer can actually be given, so it is
// labelled as such: an unfinished import leaves its questions out of every
// figure here until it is completed.
const METRICS = [["total","Available to review"],["reviewed","Reviewed"],["pending","Pending"],
  ["assigned","In progress"],["passed","Passed"],["failed","Failed"]] as const;

function Analytics({token}:{token:string}){
  const [m,setM]=useState<Metrics>(); const {batches}=useBatches(token);
  useEffect(()=>{api<Metrics>("/api/admin/analytics",token).then(setM)},[token]);
  const stuck=batches.filter(b=>b.status!=="ready");
  const held=stuck.reduce((n,b)=>n+b.stored,0);
  if(!m)return <p>Loading…</p>;
  return <>
    {held>0&&<section className="card"><p className="error">{held} uploaded question{held===1?"":"s"} are missing from these figures because {stuck.length} import{stuck.length===1?"":"s"} never finished. Complete {stuck.length===1?"it":"them"} under Upload to make {stuck.length===1?"it":"them"} reviewable.</p></section>}
    <div className="metrics">{METRICS.map(([k,label])=><section className="metric" key={k}><span>{label}</span><strong>{m[k]}</strong></section>)}</div>
    <div className="grid"><Bars title="Reviews by reviewer" rows={m.by_reviewer.map(x=>[x.reviewer,x.reviews])}/><Bars title="Reviews over time" rows={m.over_time.map(x=>[x.date,x.reviews])}/></div>
  </>;
}
function Bars({title,rows}:{title:string;rows:[string,number][]}){const max=Math.max(1,...rows.map(x=>x[1]));return <section className="card"><h2>{title}</h2>{rows.length?rows.map(([label,value])=><div className="bar" key={label}><span dir="auto">{label}</span><i><b style={{width:`${value/max*100}%`}}/></i><strong>{value}</strong></div>):<p>No completed reviews yet.</p>}</section>}

type ReviewRow = {review_id:number; source_id:string; instruction:string; question:string; output:string;
  decision:"Pass"|"Fail"; notes:string; reviewer:string; reviewed_at:string};

function Reviews({token}:{token:string}){
  const [items,setItems]=useState<ReviewRow[]>([]); const [search,setSearch]=useState("");
  const [filter,setFilter]=useState<"All"|"Pass"|"Fail">("All"); const [status,setStatus]=useState("Loading…");
  const load=useCallback(async(term:string)=>{setStatus("Loading…");try{
    setItems(await api<ReviewRow[]>(`/api/admin/reviews?search=${encodeURIComponent(term)}`,token));setStatus("");
  }catch(e){setStatus((e as Error).message)}},[token]);
  useEffect(()=>{void load("")},[load]);
  const shown=items.filter(x=>filter==="All"||x.decision===filter);
  const reset=async(row:ReviewRow)=>{
    if(!confirm(`Return item ${row.source_id} to the pending pool? The ${row.decision} decision by ${row.reviewer} is removed and another reviewer will see the question again.`))return;
    try{await api("/api/admin/reviews/reset",token,{method:"POST",body:JSON.stringify({review_id:row.review_id})});await load(search)}
    catch(e){setStatus((e as Error).message)}};
  return <section className="card"><h2>Review management</h2>
    <p className="muted">Read the question and the reviewed answer together to judge whether a decision should stand.</p>
    <div className="toolbar"><input placeholder="Search question, output, notes or reviewer" value={search}
      onChange={e=>setSearch(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")void load(search)}} />
      <button onClick={()=>void load(search)}>Search</button></div>
    <nav>{(["All","Pass","Fail"] as const).map(x=><button key={x} className={filter===x?"":"secondary"} onClick={()=>setFilter(x)}>{x}</button>)}</nav>
    <p className="muted">{status||`${shown.length} review${shown.length===1?"":"s"} shown`}</p>
    {shown.map(row=><article key={row.review_id}>
      <div className="review-head">
        <span className={row.decision==="Fail"?"tag tag-fail":"tag tag-pass"}>{row.decision}</span>
        <span><strong>{row.reviewer}</strong></span>
        <span className="muted">Source item ID: {row.source_id}</span>
        <span className="muted">{row.reviewed_at.slice(0,16).replace("T"," ")}</span>
      </div>
      <Field label="Instruction" text={row.instruction}/>
      <Field label="Question" text={row.question}/>
      <Field label="Reviewed output" text={row.output}/>
      {row.decision==="Fail"&&<Field label="Failure notes" text={row.notes}/>}
      <div><button className="danger reset" onClick={()=>reset(row)}>Reset to pending</button></div>
    </article>)}
    {!shown.length&&!status&&<p className="muted">No reviews match.</p>}
  </section>;
}

function Field({label,text}:{label:string;text:string}){
  return <div className="field"><span className="field-label">{label}</span><div className="content clamp" dir="auto">{text||"—"}</div></div>;
}

function Export({token}:{token:string}){const run=async()=>{const [{default:ExcelJS},rows]=await Promise.all([import("exceljs"),api<any[]>("/api/admin/export",token)]);const book=new ExcelJS.Workbook();const sheet=book.addWorksheet("Reviewed Data");sheet.columns=["instruction","question","output","pass/fail","notes"].map(key=>({header:key,key,width:36}));rows.forEach(row=>sheet.addRow(row));sheet.getRow(1).font={bold:true,color:{argb:"FFFFFFFF"}};sheet.getRow(1).fill={type:"pattern",pattern:"solid",fgColor:{argb:"FF164E63"}};sheet.eachRow(row=>row.alignment={wrapText:true,vertical:"top"});const data=await book.xlsx.writeBuffer();const url=URL.createObjectURL(new Blob([data],{type:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}));const a=document.createElement("a");a.href=url;a.download="reviewed_data.xlsx";a.click();URL.revokeObjectURL(url)};return <section className="card"><h2>Export reviewed data</h2><p>Generate the five-column XLSX file locally in this browser.</p><button onClick={run}>Download reviewed_data.xlsx</button></section>}

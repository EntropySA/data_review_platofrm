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

function Upload({token}:{token:string}) {
  const [status,setStatus]=useState(""); const [errors,setErrors]=useState<{row:number;message:string}[]>([]);
  const upload=async(file:File)=>{try{const text=await file.text();const parsed=validateDocument(JSON.parse(text));setErrors(parsed.errors);const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(text)))).map(x=>x.toString(16).padStart(2,"0")).join("");
    const start=await api<{batch_id:number}>("/api/admin/imports",token,{method:"POST",body:JSON.stringify({filename:file.name,file_hash:hash})});for(let i=0;i<parsed.valid.length;i+=200){setStatus(`Uploading ${Math.min(i+200,parsed.valid.length)} / ${parsed.valid.length}`);await api(`/api/admin/imports/${start.batch_id}/records`,token,{method:"POST",body:JSON.stringify({records:parsed.valid.slice(i,i+200),skipped_count:i===0?parsed.errors.length:0})})}await api(`/api/admin/imports/${start.batch_id}/finish`,token,{method:"POST"});setStatus(`Imported ${parsed.valid.length}; skipped ${parsed.errors.length}.`)}catch(e){setStatus((e as Error).message)}};
  return <section className="card"><h2>Upload questions</h2><input type="file" accept="application/json,.json" onChange={e=>e.target.files?.[0]&&upload(e.target.files[0])}/><p>{status}</p>{errors.slice(0,100).map(e=><p className="error" key={e.row}>Row {e.row}: {e.message}</p>)}</section>;
}

function Analytics({token}:{token:string}){const [m,setM]=useState<Metrics>();useEffect(()=>{api<Metrics>("/api/admin/analytics",token).then(setM)},[token]);if(!m)return <p>Loading…</p>;return <><div className="metrics">{(["total","reviewed","pending","assigned","passed","failed"] as const).map(k=><section className="metric" key={k}><span>{k}</span><strong>{m[k]}</strong></section>)}</div><div className="grid"><Bars title="Reviews by reviewer" rows={m.by_reviewer.map(x=>[x.reviewer,x.reviews])}/><Bars title="Reviews over time" rows={m.over_time.map(x=>[x.date,x.reviews])}/></div></>}
function Bars({title,rows}:{title:string;rows:[string,number][]}){const max=Math.max(1,...rows.map(x=>x[1]));return <section className="card"><h2>{title}</h2>{rows.length?rows.map(([label,value])=><div className="bar" key={label}><span dir="auto">{label}</span><i><b style={{width:`${value/max*100}%`}}/></i><strong>{value}</strong></div>):<p>No completed reviews yet.</p>}</section>}

function Reviews({token}:{token:string}){const [items,setItems]=useState<any[]>([]);const [search,setSearch]=useState("");const load=()=>api<any[]>(`/api/admin/reviews?search=${encodeURIComponent(search)}`,token).then(setItems);useEffect(()=>{void load()},[token]);return <section className="card"><h2>Review management</h2><div className="actions"><input placeholder="Search" value={search} onChange={e=>setSearch(e.target.value)}/><button onClick={load}>Search</button></div>{items.map(x=><article key={x.review_id}><strong>{x.decision} · {x.reviewer} · {x.source_id}</strong><div dir="auto">{x.notes||"—"}</div><button className="danger" onClick={async()=>{if(confirm("Return this question to pending?")){await api("/api/admin/reviews/reset",token,{method:"POST",body:JSON.stringify({review_id:x.review_id})});load()}}}>Reset</button></article>)}</section>}

function Export({token}:{token:string}){const run=async()=>{const [{default:ExcelJS},rows]=await Promise.all([import("exceljs"),api<any[]>("/api/admin/export",token)]);const book=new ExcelJS.Workbook();const sheet=book.addWorksheet("Reviewed Data");sheet.columns=["instruction","question","output","pass/fail","notes"].map(key=>({header:key,key,width:36}));rows.forEach(row=>sheet.addRow(row));sheet.getRow(1).font={bold:true,color:{argb:"FFFFFFFF"}};sheet.getRow(1).fill={type:"pattern",pattern:"solid",fgColor:{argb:"FF164E63"}};sheet.eachRow(row=>row.alignment={wrapText:true,vertical:"top"});const data=await book.xlsx.writeBuffer();const url=URL.createObjectURL(new Blob([data],{type:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}));const a=document.createElement("a");a.href=url;a.download="reviewed_data.xlsx";a.click();URL.revokeObjectURL(url)};return <section className="card"><h2>Export reviewed data</h2><p>Generate the five-column XLSX file locally in this browser.</p><button onClick={run}>Download reviewed_data.xlsx</button></section>}

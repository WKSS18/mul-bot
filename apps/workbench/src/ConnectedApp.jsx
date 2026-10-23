import React, {useEffect, useRef, useState} from 'react';
import {Bot, MessageSquare, Plus, Send, Users, Paperclip, LogOut, Globe, CheckCircle2} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './mentions.css';
import './bot-picker.css';

const base = window.tongpinDesktop?.apiBaseUrl || import.meta.env.VITE_API_BASE_URL || '/api/v1';

async function request(path, options = {}, token) {
  const desktop = window.tongpinDesktop;
  const result = desktop?.request
    ? await desktop.request(path, options)
    : await fetch(base + path, {...options, headers: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {}), ...options.headers}});
  const json = desktop?.request ? result.data : await result.json().catch(() => ({}));
  const ok = desktop?.request ? result.status >= 200 && result.status < 300 : result.ok;
  if (!ok) throw new Error(json.error?.message || `请求失败 (${result.status})`);
  return json.data;
}

function csrfToken(){return decodeURIComponent(document.cookie.split('; ').find(part=>part.startsWith('tp_csrf='))?.split('=')[1]||'');}

const markMentions = (value) => String(value||'').split(/(@[^\s@，。！？：；、]+)/g).map((part,index) => part.startsWith('@') ? <span className="bot-mention" key={index}>{part}</span> : part);

function MarkdownMessage({children}) {
  return <div className="connected-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{a:({children:label,...props})=><a {...props} target="_blank" rel="noreferrer">{label}</a>,img:()=>null,text:({children})=>markMentions(children)}}>{children || ''}</ReactMarkdown></div>;
}

export function ConnectedApp() {
  const [token, setToken] = useState('');
  const [user, setUser] = useState(null);
  const [wid, setWid] = useState('');
  const [rooms, setRooms] = useState([]);
  const [page, setPage] = useState('chat');
  const [records, setRecords] = useState([]);
  const [recordQuery, setRecordQuery] = useState('');
  const [recordRoom, setRecordRoom] = useState('');
  const [botSearch, setBotSearch] = useState('');
  const [members, setMembers] = useState([]);
  const [room, setRoom] = useState(null);
  const [messages, setMessages] = useState([]);
  const [bots, setBots] = useState([]);
  const [roomBots, setRoomBots] = useState([]);
  const [selectedBots, setSelectedBots] = useState([]);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [authMode, setAuthMode] = useState('login');
  const [dialog, setDialog] = useState('');
  const [form, setForm] = useState({email:'',password:'',display_name:'',name:'',prompt:'',description:''});
  const [runStatus, setRunStatus] = useState('');
  const [toolEvents, setToolEvents] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [failedUpload, setFailedUpload] = useState(null);
  const [inviteToken, setInviteToken] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [preview, setPreview] = useState(null);
  const [editBot, setEditBot] = useState(null);
  const [testBot, setTestBot] = useState(null);
  const [testPrompt, setTestPrompt] = useState('');
  const [testRunId, setTestRunId] = useState('');
  const [testResult, setTestResult] = useState(null);
  const lastSeq = useRef(0);
  const roomRef = useRef(null);
  const scrollRef = useRef(null);
  const targetRecordRef = useRef(null);
  const workspaceLoadId = useRef(0);

  useEffect(()=>{
    let closed=false;
    const csrf=csrfToken();
    if(!csrf&&!window.tongpinDesktop)return;
    request('/auth/refresh',{method:'POST',headers:{'X-CSRF-Token':csrf}}).then(async data=>{
      const me=await request('/me',{},data.access_token);
      if(!closed){setToken(data.access_token);setUser(me);setWid(me.workspaces[0]?.id||'');}
    }).catch(()=>{});
    return()=>{closed=true};
  },[]);

  useEffect(() => { roomRef.current = room; }, [room]);
  useEffect(() => {
    const target=targetRecordRef.current;
    if(target && messages.some(item=>item.id===target.id)){
      document.getElementById(`connected-msg-${target.id}`)?.scrollIntoView({block:'center'});
      targetRecordRef.current=null;
    }else scrollRef.current?.scrollTo?.({top: scrollRef.current.scrollHeight});
  }, [messages]);
  useEffect(()=>{
    if(!room || !token || !messages.length)return;
    const seq=messages[messages.length-1]?.message_seq;
    if(!seq)return;
    const timer=setTimeout(()=>request(`/rooms/${room.id}/read-position`,{method:'POST',body:JSON.stringify({message_seq:seq})},token).catch(()=>{}),350);
    return()=>clearTimeout(timer);
  },[room?.id,token,messages]);
  useEffect(()=>{
    if(page!=='records' || !token)return;
    let closed=false;
    const timer=setTimeout(()=>{
      const params=new URLSearchParams();
      if(recordRoom)params.set('room_id',recordRoom);
      if(recordQuery.trim())params.set('q',recordQuery.trim());
      request(`/records?${params}`,{},token).then(rows=>{if(!closed)setRecords(rows)}).catch(exc=>{if(!closed)setError(exc.message)});
    },250);
    return()=>{closed=true;clearTimeout(timer)};
  },[page,recordQuery,recordRoom,token]);
  useEffect(()=>{
    if(!testRunId || dialog!=='test' || ['COMPLETED','FAILED'].includes(testResult?.status))return;
    let closed=false;
    const poll=async()=>{
      try{const result=await request(`/bot-test-runs/${testRunId}`,{},token);if(!closed)setTestResult(result);}
      catch(exc){if(!closed)setError(exc.message);}
    };
    poll();const timer=setInterval(poll,1200);
    return()=>{closed=true;clearInterval(timer);};
  },[testRunId,dialog,token,testResult?.status]);

  async function authenticate(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const data = await request(authMode === 'login' ? '/auth/login' : '/auth/register', {method:'POST', body:JSON.stringify(form)});
      const me = await request('/me', {}, data.access_token);
      setToken(data.access_token); setUser(me); setWid(me.workspaces[0]?.id || '');
    } catch (exc) { setError(exc.message); } finally { setBusy(false); }
  }

  useEffect(() => {
    if (!token || !wid) return;
    let closed = false;
    const loadId = ++workspaceLoadId.current;
    Promise.all([request(`/workspaces/${wid}/rooms`, {}, token), request(`/bots?workspace_id=${wid}`, {}, token)])
      .then(([rs, bs]) => { if (!closed && loadId === workspaceLoadId.current) {setRooms(rs); setBots(bs); setRoom(rs[0] || null);} })
      .catch(exc => !closed && setError(exc.message));
    return () => {closed = true;};
  }, [token, wid]);

  useEffect(() => {
    if (!room || !token) return;
    let closed = false;
    lastSeq.current = 0;
    setMessages([]);
    setToolEvents([]);
    setSelectedBots([]);
    request(`/rooms/${room.id}/bots`, {}, token).then(rows => {if (!closed) setRoomBots(rows);}).catch(exc => setError(exc.message));
    let socket;
    let retry;
    async function connect() {
      try {
        const bootstrap=await request(`/rooms/${room.id}/bootstrap`,{},token);
        if(closed)return;
        let history=bootstrap.messages;
        const target=targetRecordRef.current;
        if(target?.room_id===room.id)history=await request(`/rooms/${room.id}/messages?before_seq=${target.message_seq+1}&limit=50`,{},token);
        if(closed)return;
        setMessages(history);
        lastSeq.current=bootstrap.snapshot_seq;
        const {ticket} = await request('/realtime/tickets', {method:'POST'}, token);
        if (closed) return;
        const endpoint = window.tongpinDesktop?.wsUrl ? new URL(window.tongpinDesktop.wsUrl) : new URL(base + '/realtime', location.href);
        if(!window.tongpinDesktop) endpoint.protocol = endpoint.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(endpoint);
        socket.onopen = () => socket.send(JSON.stringify({type:'auth', ticket}));
        socket.onmessage = event => {
          const data = JSON.parse(event.data);
          if (data.type === 'authenticated') socket.send(JSON.stringify({type:'subscribe',room_id:room.id,after_seq:lastSeq.current}));
          if (data.seq) lastSeq.current = Math.max(lastSeq.current, data.seq);
          if (data.type === 'message.created') setMessages(items => items.some(item => item.id === data.payload.message_id) ? items : [...items, {id:data.payload.message_id,message_seq:data.payload.message_seq,body:data.payload.body,sender_type:data.payload.sender_type || 'USER',bot_id:data.payload.bot_id,sender_id:data.payload.sender_id,attachments:data.payload.attachments||[]}]);
          if (data.type === 'message.delta' || data.type === 'message.completed') setMessages(items => items.map(item => item.id===data.payload.message_id?{...item,body:data.payload.body,attachments:data.payload.attachments||item.attachments}:item));
          if (data.type === 'tool.completed') setToolEvents(items => items.some(item=>item.seq===data.seq)?items:[...items,{seq:data.seq,...data.payload}]);
          if (data.type?.startsWith('run.')) setRunStatus(data.type === 'run.completed' ? 'Bot 协作已完成' : data.type === 'run.failed' ? (data.payload?.code === 'MODEL_FAILED' ? '模型调用失败，请检查模型配置或稍后重试' : 'Bot 执行失败，请稍后重试') : 'Bot 正在处理');
        };
        socket.onclose = () => {if (!closed) retry = setTimeout(connect, 1200);};
      } catch (exc) {if (!closed) {setError(exc.message); retry = setTimeout(connect, 1500);}}
    }
    connect();
    return () => {closed = true; clearTimeout(retry); socket?.close();};
  }, [room?.id, token]);

  async function makeRoom(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {workspaceLoadId.current += 1; const created = await request(`/workspaces/${wid}/rooms`, {method:'POST', body:JSON.stringify({name:form.name})}, token); setRooms(list => [created, ...list]); setRoom(created); setDialog(''); setForm({...form,name:''});}
    catch (exc) {setError(exc.message);} finally {setBusy(false);}
  }

  async function makeBot(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      if (editBot) {
        const saved = await request(`/bots/${editBot.id}`, {method:'PUT',body:JSON.stringify({name:form.name,description:form.description,prompt:form.prompt,tools:form.tools||[],revision:editBot.revision})}, token);
        setBots(list => list.map(item => item.id===editBot.id?{...item,name:form.name,description:form.description,revision:saved.revision}:item));
        setRoomBots(list => list.map(item => item.id===editBot.id?{...item,name:form.name}:item));
      } else {
        const created = await request('/bots', {method:'POST',body:JSON.stringify({workspace_id:wid,name:form.name,description:form.description,prompt:form.prompt,tools:form.tools||[]})}, token);
        setBots(list => [...list,{...created,description:form.description,enabled:true,revision:1}]);
      }
      setEditBot(null); setDialog(''); setForm({...form,name:'',prompt:'',description:'',tools:[]});
    }
    catch (exc) {setError(exc.message);} finally {setBusy(false);}
  }

  async function addBot(botId) {
    if (!room) return;
    try {await request(`/rooms/${room.id}/bots`, {method:'POST',body:JSON.stringify({bot_id:botId})}, token); const bot=bots.find(item=>item.id===botId);setRoomBots(items=>items.some(item=>item.id===botId)?items:[...items,bot]); setSelectedBots(ids => ids.includes(botId) ? ids : [...ids,botId].slice(0,3));}
    catch (exc) {setError(exc.message);}
  }

  async function removeBot(botId) {
    if (!room) return;
    try { await request(`/rooms/${room.id}/bots/${botId}`, {method:'DELETE'}, token); setRoomBots(items=>items.filter(item=>item.id!==botId)); setSelectedBots(ids=>ids.filter(id=>id!==botId)); }
    catch (exc) {setError(exc.message);}
  }

  async function startEditBot(bot) {
    try {const detail=await request(`/bots/${bot.id}`,{},token); setEditBot(detail); setForm({...form,name:detail.name,description:detail.description,prompt:detail.prompt,tools:detail.tools}); setDialog('bot');}
    catch(exc){setError(exc.message);}
  }

  async function toggleBot(bot) {
    try {const updated=await request(`/bots/${bot.id}/state`,{method:'PATCH',body:JSON.stringify({enabled:!bot.enabled,revision:bot.revision})},token); setBots(items=>items.map(item=>item.id===bot.id?{...item,...updated}:item)); setRoomBots(items=>items.map(item=>item.id===bot.id?{...item,enabled:updated.enabled}:item));}
    catch(exc){setError(exc.message);}
  }

  async function startBotTest(event) {
    event.preventDefault();setBusy(true);setError('');setTestResult(null);
    try{const created=await request(`/bots/${testBot.id}/test-runs`,{method:'POST',body:JSON.stringify({prompt:testPrompt})},token);setTestRunId(created.id);}
    catch(exc){setError(exc.message);}finally{setBusy(false);}
  }

  const mentionQuery = text.match(/@([^@\s]*)$/)?.[1]?.toLowerCase() || '';
  const mentionMatches = roomBots.filter(bot => bot.enabled && bot.name.toLowerCase().includes(mentionQuery));

  function chooseMention(bot) {
    const match = text.match(/@[^@\s]*$/);
    if (match) setText(text.slice(0, -match[0].length) + `@${bot.name} `);
    setSelectedBots(ids => ids.includes(bot.id) ? ids : [...ids, bot.id].slice(0, 3));
    setMentionOpen(false);
  }

  function composerKeyDown(event) {
    if (event.nativeEvent.isComposing || !mentionOpen) return;
    if (event.key === 'Escape') {event.preventDefault();setMentionOpen(false);}
    if (event.key === 'ArrowDown') {event.preventDefault();setMentionIndex(index => (index + 1) % Math.max(mentionMatches.length, 1));}
    if (event.key === 'ArrowUp') {event.preventDefault();setMentionIndex(index => (index - 1 + mentionMatches.length) % Math.max(mentionMatches.length, 1));}
    if (event.key === 'Enter' && mentionMatches[mentionIndex]) {event.preventDefault();chooseMention(mentionMatches[mentionIndex]);}
  }

  async function send(event) {
    event.preventDefault(); if (!room || (!text.trim() && !attachments.length) || busy) return;
    setBusy(true); setError('');
    try {
      const value = text.trim();
      const mentions = selectedBots.map((bot_id,position) => ({bot_id,position}));
      const result = await request(`/rooms/${room.id}/messages`, {method:'POST',body:JSON.stringify({client_message_id:crypto.randomUUID(),text:value,mentions,attachment_ids:attachments.map(item=>item.id)})}, token);
      setMessages(items => items.some(item=>item.id===result.message_id)?items:[...items,{id:result.message_id,message_seq:result.message_seq,body:value,sender_type:'USER',sender_id:user.id,attachments}]);
      setText(''); setSelectedBots([]); setAttachments([]);
      if (result.run_id) setRunStatus('Bot 已进入执行队列');
    } catch (exc) {setError(exc.message);} finally {setBusy(false);}
  }

  async function uploadFile(file, existingFileId=null) {
    if (!file) return;
    setBusy(true); setError('');
    let fileId=existingFileId;
    try {
      setFailedUpload(null);
      setUploadProgress({name:file.name,percent:0});
      const extension=file.name.split('.').pop()?.toLowerCase();
      const fallbackMime={pdf:'application/pdf',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',txt:'text/plain',md:'text/markdown',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg'}[extension];
      const init = existingFileId
        ? await request(`/uploads/${existingFileId}/resign`,{method:'POST'},token)
        : await request('/uploads/init', {method:'POST',body:JSON.stringify({workspace_id:wid,filename:file.name,mime:file.type||fallbackMime||'application/octet-stream',size:file.size})}, token);
      fileId=init.file_id;
      const body = new FormData();
      Object.entries(init.upload.fields).forEach(([key,value])=>body.append(key,value));
      body.append('file', file);
      await new Promise((resolve,reject)=>{
        const xhr=new XMLHttpRequest();
        xhr.open('POST',init.upload.url);
        xhr.upload.onprogress=event=>{if(event.lengthComputable)setUploadProgress({name:file.name,percent:Math.round(event.loaded/event.total*100)});};
        xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve():reject(new Error(`OSS 上传失败 (${xhr.status})`));
        xhr.onerror=()=>reject(new Error('上传网络异常'));
        xhr.send(body);
      });
      await request(`/uploads/${init.file_id}/complete`,{method:'POST'},token);
      setAttachments(items=>[...items,{id:init.file_id,filename:file.name}]);
    } catch(exc) {setError(exc.message);setFailedUpload({file,fileId});} finally {setBusy(false);setUploadProgress(null);}
  }

  async function openFile(file) {
    try {const access=await request(`/files/${file.id}/access?mode=preview`,{},token); setPreview({id:file.id,url:access.url,filename:file.filename,mime:file.mime||''});}
    catch(exc){setError(exc.message);}
  }

  async function downloadFile(file) {
    try {
      if(window.tongpinDesktop?.saveFile) return await window.tongpinDesktop.saveFile(file.id,file.filename);
      const access=await request(`/files/${file.id}/access?mode=download`,{},token);
      const link=document.createElement('a');link.href=access.url;link.rel='noopener noreferrer';link.click();
    } catch(exc){setError(exc.message);}
  }

  async function createInvitation(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {const result=await request(`/rooms/${room.id}/invitations`,{method:'POST',body:JSON.stringify({email:form.email})},token); setInviteToken(result.invitation_token);}
    catch(exc){setError(exc.message);} finally{setBusy(false);}
  }

  async function acceptInvitation(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {const result=await request('/invitations/accept',{method:'POST',body:JSON.stringify({token:inviteCode})},token); const list=await request(`/workspaces/${result.workspace_id}/rooms`,{},token); setWid(result.workspace_id); setRooms(list); setRoom(list.find(item=>item.id===result.room_id)||list[0]); setDialog(''); setInviteCode('');}
    catch(exc){setError(exc.message);} finally{setBusy(false);}
  }

  async function openMembers() {
    if (!room) return;
    try {setMembers(await request(`/rooms/${room.id}/members`,{},token));setDialog('members');}
    catch(exc){setError(exc.message);}
  }

  async function removeMember(memberId) {
    try {await request(`/rooms/${room.id}/members/${memberId}`,{method:'DELETE'},token);setMembers(items=>items.filter(item=>item.id!==memberId));}
    catch(exc){setError(exc.message);}
  }

  async function renameRoom(event) {
    event.preventDefault();setBusy(true);setError('');
    try {const updated=await request(`/rooms/${room.id}`,{method:'PATCH',body:JSON.stringify({name:form.name,revision:room.revision})},token);setRooms(items=>items.map(item=>item.id===room.id?updated:item));setRoom(updated);setDialog('');}
    catch(exc){setError(exc.message);}finally{setBusy(false);}
  }

  async function archiveRoom() {
    if (!room || !window.confirm('归档后聊天室只能查看历史，确定归档？')) return;
    try {const archived=await request(`/rooms/${room.id}/archive`,{method:'POST'},token);const updated={...room,...archived};setRoom(updated);setRooms(items=>items.map(item=>item.id===room.id?updated:item));setDialog('');}
    catch(exc){setError(exc.message);}
  }

  async function transferOwner(memberId) {
    if (!window.confirm('确定将房主转让给这位成员？')) return;
    try {await request(`/rooms/${room.id}/transfer-owner`,{method:'POST',body:JSON.stringify({user_id:memberId})},token);setMembers(await request(`/rooms/${room.id}/members`,{},token));}
    catch(exc){setError(exc.message);}
  }

  async function openRecord(record) {
    targetRecordRef.current=record;
    setPage('chat');
    if(room?.id===record.room_id){
      try{setMessages(await request(`/rooms/${record.room_id}/messages?before_seq=${record.message_seq+1}&limit=50`,{},token));}
      catch(exc){setError(exc.message);}
    }else{
      const next=rooms.find(item=>item.id===record.room_id);
      if(next)setRoom(next);
    }
  }

  if (!token) return <div className="auth-screen"><div className="auth-card"><img src="/assets/brand.png" alt=""/><h1>同频 AI</h1><p>团队与 AI，在同一个聊天室协作</p><form onSubmit={authenticate}><h2>{authMode==='login'?'登录工作台':'创建账户'}</h2>{authMode==='register'&&<input required placeholder="昵称" value={form.display_name} onChange={e=>setForm({...form,display_name:e.target.value})}/>}<input required type="email" placeholder="邮箱" value={form.email} onChange={e=>setForm({...form,email:e.target.value})}/><input required type="password" minLength={authMode==='register'?12:1} placeholder="密码" value={form.password} onChange={e=>setForm({...form,password:e.target.value})}/>{error&&<div role="alert" className="auth-error">{error}</div>}<button disabled={busy} className="button button-primary">{busy?'请稍候':authMode==='login'?'登录':'注册并进入'}</button></form><button className="auth-switch" onClick={()=>{setAuthMode(authMode==='login'?'register':'login');setError('')}}>{authMode==='login'?'没有账户？立即注册':'已有账户？去登录'}</button><a href={import.meta.env.VITE_WEBSITE_URL||(import.meta.env.PROD?'/':'http://localhost:5173/')}><Globe size={15}/>返回官网</a></div></div>;

  return <div className="connected-layout"><aside className="connected-sidebar"><div className="connected-brand"><img src="/assets/brand.png" alt=""/><b>同频 AI</b></div><button className="button button-primary" onClick={()=>{setForm({...form,name:''});setDialog('room')}}><Plus size={16}/>新建聊天室</button><h3>聊天室</h3>{rooms.map(item=><button key={item.id} className={'connected-room '+(item.id===room?.id?'active':'')} onClick={()=>{setRoom(item);setPage('chat')}}><MessageSquare size={17}/>{item.name}</button>)}<div className="connected-side-bottom"><button onClick={()=>setPage('records')}>协作记录</button><button onClick={()=>{setEditBot(null);setForm({...form,name:'',description:'',prompt:'',tools:[]});setDialog('bot')}}><Bot size={17}/>创建 Bot</button><button onClick={()=>setDialog('accept')}><Users size={17}/>接受邀请</button><a href={import.meta.env.VITE_WEBSITE_URL||(import.meta.env.PROD?'/':'http://localhost:5173/')}><Globe size={17}/>产品官网</a><button onClick={async()=>{try{await request('/auth/logout',{method:'POST',headers:{'X-CSRF-Token':csrfToken()}},token)}catch{}setToken('');setUser(null);setRoom(null);setMessages([])}}><LogOut size={17}/>退出登录</button></div></aside><main className="connected-main"><header><div><h2>{room?.name||'欢迎来到同频 AI'}</h2><small>{room?'团队消息与 Bot 任务实时同步':'创建聊天室开始协作'}</small></div><div className="connected-header-actions">{room&&<button className="button button-outline" onClick={()=>{setInviteToken('');setDialog('invite')}}><Users size={15}/>邀请成员</button>}{room&&<><button className="button button-outline" onClick={openMembers}>成员</button><button className="button button-outline" onClick={()=>{setForm({...form,name:room.name});setDialog('rename')}}>设置</button></>}<span className="connected-user">{user.display_name}</span></div></header>{error&&<div role="alert" className="connected-error">{error}<button onClick={()=>setError('')}>关闭</button></div>}{page==='records'?<div className="connected-records"><h2>协作记录</h2><div className="connected-record-filters"><input aria-label="搜索记录" placeholder="搜索消息内容" value={recordQuery} onChange={e=>setRecordQuery(e.target.value)}/><select aria-label="筛选聊天室" value={recordRoom} onChange={e=>setRecordRoom(e.target.value)}><option value="">全部聊天室</option>{rooms.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></div>{records.length?records.map(record=><button key={record.id} onClick={()=>openRecord(record)}><strong>{record.room_name}</strong><time>{record.created_at.slice(0,19).replace('T',' ')}</time><p>{record.body||'文件或任务消息'}</p></button>):<p>没有找到记录</p>}</div>:<><div className="connected-messages" ref={scrollRef}>{!room?<div className="connected-empty">创建第一个聊天室，开始团队协作。</div>:messages.length===0?<div className="connected-empty">还没有消息。发送一条消息，或添加 Bot 参与讨论。</div>:messages.map(item=><article id={`connected-msg-${item.id}`} key={item.id} className="connected-message"><span className={'connected-avatar '+(item.sender_type==='BOT'?'is-bot':'')}>{item.sender_type==='BOT'?<Bot size={18}/>:user.display_name?.slice(0,1)}</span><div><b>{item.sender_type==='BOT'?bots.find(b=>b.id===item.bot_id)?.name||'Bot':item.sender_id===user.id?user.display_name:'团队成员'}</b>{item.sender_type==='BOT'?<MarkdownMessage>{item.body}</MarkdownMessage>:<p className="connected-plain">{markMentions(item.body)}</p>}{item.attachments?.map(file=><span key={file.id}><button className="connected-file" onClick={()=>openFile(file)}><Paperclip size={14}/>{file.filename}</button><button type="button" onClick={()=>downloadFile(file)}>下载</button></span>)}</div></article>)}</div>{room&&!room.archived&&<div className="connected-composer"><div className="connected-bots"><span>协作 Bot：</span>{roomBots.map(bot=><button type="button" className="connected-bot-chip" key={bot.id} onClick={()=>removeBot(bot.id)} title="点击移出房间">@{bot.name} ×</button>)}<button type="button" className="connected-add-bot" onClick={()=>setDialog("picker")}>＋ 添加 Bot</button></div>{uploadProgress&&<div className="connected-upload-progress" role="status">{uploadProgress.name} · 上传 {uploadProgress.percent}%<progress value={uploadProgress.percent} max="100"/></div>}{failedUpload&&<button type="button" className="connected-upload-retry" onClick={()=>uploadFile(failedUpload.file,failedUpload.fileId)}>重试上传 {failedUpload.file.name}</button>}{attachments.length>0&&<div className="connected-pending">{attachments.map(file=><span key={file.id}>{file.filename}<button onClick={()=>setAttachments(items=>items.filter(item=>item.id!==file.id))}>×</button></span>)}</div>}{toolEvents.length>0&&<details className="connected-tools"><summary>工具过程（{toolEvents.length}）</summary>{toolEvents.map(event=><div key={event.seq}>{event.tool} · 已完成{event.expression&&' · '+event.expression+' = '+event.result}{event.format&&' · '+event.format}{event.count&&' · '+event.count+' 个附件'}</div>)}</details>}{runStatus&&<div className="connected-run"><CheckCircle2 size={14}/>{runStatus}</div>}{mentionOpen&&<div className="connected-mentions" role="listbox">{mentionMatches.map((bot,index)=><button type="button" role="option" aria-selected={index===mentionIndex} key={bot.id} onClick={()=>chooseMention(bot)}>@{bot.name}</button>)}{!mentionMatches.length&&<span>房间内没有匹配的 Bot</span>}</div>}<form onSubmit={send}>{window.tongpinDesktop?.uploadFile?<button type="button" className="connected-attach" title="上传附件" onClick={async()=>{try{const chosen=await window.tongpinDesktop.uploadFile(wid);if(chosen)setAttachments(items=>[...items,chosen]);}catch(exc){setError(exc.message)}}}><Paperclip size={18}/></button>:<label className="connected-attach" title="上传附件"><Paperclip size={18}/><input type="file" accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg" onChange={e=>{uploadFile(e.target.files?.[0]);e.target.value='';}}/></label>}<textarea className={text.includes("@")?"connected-composer-input has-mention":"connected-composer-input"} aria-label="聊天消息" value={text} onChange={e=>{setText(e.target.value);setMentionOpen(/@[^@\s]*$/.test(e.target.value));setMentionIndex(0)}} onKeyDown={composerKeyDown} placeholder="输入消息；键入 @ 可选择 Bot"/><button className="button button-primary" disabled={busy||(!text.trim()&&!attachments.length)}><Send size={16}/>发送</button></form></div>}</>}</main>{preview&&<div className="connected-modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setPreview(null)}}><div className="connected-preview"><header><strong>预览：{preview.filename}</strong><div><button type="button" onClick={()=>downloadFile(preview)}>下载</button><button type="button" onClick={()=>setPreview(null)}>关闭</button></div></header>{preview.mime.includes('pdf')?<iframe title={preview.filename} src={preview.url}/>:preview.mime.startsWith('image/')?<img src={preview.url} alt={preview.filename}/>:<div>此格式请下载后查看。</div>}</div></div>}{dialog&&<div className="connected-modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setDialog('')}}><form className="connected-modal" onSubmit={dialog==='room'?makeRoom:dialog==='bot'?makeBot:dialog==='invite'?createInvitation:dialog==='rename'?renameRoom:dialog==='test'?startBotTest:dialog==='members'||dialog==='picker'?event=>event.preventDefault():acceptInvitation}><h2>{dialog==='room'?'新建聊天室':dialog==='bot'?(editBot?'编辑 Bot':'创建 Bot'):dialog==='invite'?'邀请成员':dialog==='rename'?'聊天室设置':dialog==='members'?'聊天室成员':dialog==='picker'?'选择聊天室 Bot':dialog==='test'?'测试 Bot':'接受邀请'}</h2>{dialog==='picker'&&<div className="connected-bot-picker"><input aria-label="搜索 Bot" placeholder="搜索 Bot" value={botSearch} onChange={e=>setBotSearch(e.target.value)}/>{bots.filter(bot=>bot.name.includes(botSearch)).map(bot=><div className="connected-bot-option" key={bot.id}><div><strong>{bot.name}</strong><small>{bot.description||"未填写简介"}</small></div><button type="button" className="button button-small" onClick={()=>roomBots.some(item=>item.id===bot.id)?removeBot(bot.id):addBot(bot.id)}>{roomBots.some(item=>item.id===bot.id)?"移出":"加入"}</button><button type="button" className="button button-small button-quiet" onClick={()=>startEditBot(bot)}>编辑</button></div>)}</div>}{(dialog==='room'||dialog==='bot'||dialog==='rename')&&<label>名称<input required minLength={2} maxLength={80} value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>}{dialog==='bot'&&<><label>简介<input maxLength={255} value={form.description} onChange={e=>setForm({...form,description:e.target.value})}/></label><label>角色指令<textarea required minLength={10} value={form.prompt} onChange={e=>setForm({...form,prompt:e.target.value})}/></label><fieldset><legend>允许的工具</legend>{[['read_attachment','读取附件'],['calculator','计算器'],['generate_document','生成文件'],['handoff_to_bot','交接给下一 Bot']].map(([id,label])=><label key={id}><input type="checkbox" checked={(form.tools||[]).includes(id)} onChange={e=>setForm({...form,tools:e.target.checked?[...(form.tools||[]),id]:(form.tools||[]).filter(item=>item!==id)})}/>{label}</label>)}</fieldset></>}{dialog==='invite'&&<><label>受邀邮箱<input type="email" required value={form.email} onChange={e=>setForm({...form,email:e.target.value})}/></label>{inviteToken&&<div className="connected-invite-code">邀请口令（请私下发给受邀人）：<code>{inviteToken}</code><button type="button" onClick={()=>navigator.clipboard.writeText(inviteToken)}>复制口令</button></div>}</>}{dialog==='rename'&&<button type="button" className="button button-outline" onClick={archiveRoom}>归档聊天室</button>}{dialog==='test'&&<><p>测试使用已保存版本，不写入聊天室。</p><label>测试输入<textarea required maxLength={2000} value={testPrompt} onChange={e=>setTestPrompt(e.target.value)}/></label>{testResult&&<div role="status">状态：{testResult.status}{testResult.output&&<p>{testResult.output}</p>}{testResult.error&&<p>失败：{testResult.error}</p>}</div>}</>}{dialog==='members'&&<div className="connected-member-list">{members.map(member=><div key={member.id}><span>{member.display_name} · {member.role}</span>{member.role!=='OWNER'&&members.find(item=>item.id===user.id)?.role==='OWNER'&&<><button type="button" onClick={()=>transferOwner(member.id)}>转让房主</button><button type="button" onClick={()=>removeMember(member.id)}>移除</button></>}{member.id===user.id&&member.role!=='OWNER'&&<button type="button" onClick={()=>removeMember(member.id)}>离开</button>}</div>)}</div>}{dialog==='accept'&&<label>邀请口令<input required value={inviteCode} onChange={e=>setInviteCode(e.target.value)}/></label>}{error&&<div role="alert">{error}</div>}<div className="connected-actions"><button type="button" className="button button-outline" onClick={()=>setDialog('')}>取消</button>{dialog!=='members'&&dialog!=='picker'&&<button className="button button-primary" disabled={busy}>{dialog==='test'?'开始测试':'保存'}</button>}</div></form></div>}</div>;
}

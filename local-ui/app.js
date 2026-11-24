const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[char]));
const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const state = { view:'chat', logs:[], docPage:1, docTotal:0, selectedDoc:null, chunkOffset:0, chunkCount:0, chatAbort:null, uploadWatch:null, endpoints:[], healthBusy:false, sessionToken:localStorage.getItem('nursingbot-local-token')||null };
let toastTimer;

async function ensureSession() {
  if (state.sessionToken) return state.sessionToken;
  try {
    const res = await fetch('/api/chat/session', {method:'POST', headers:{'Content-Type':'application/json'}}).then(r=>r.json());
    if (res.session_token) {
      state.sessionToken = res.session_token;
      updateSession(res.session_id);
      localStorage.setItem('nursingbot-local-token', res.session_token);
      return res.session_token;
    }
  } catch(e) {
    console.warn('Could not auto-create session token:', e);
  }
  return null;
}

function toast(message, error = false) {
  clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').className = `toast${error ? ' error' : ''}`;
  toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), error ? 9000 : 4500);
}
function logRequest(record) {
  record.id = crypto.randomUUID(); record.time = new Date().toLocaleTimeString();
  state.logs.unshift(record); state.logs = state.logs.slice(0, 100); $('#log-count').textContent = state.logs.length;
  $('#log-list').innerHTML = state.logs.map(item => `<button class="log-row" data-log="${item.id}"><b>${escape(item.method)}</b>${escape(item.path)}<span class="log-status">${escape(item.status)}</span><small>${item.time} · ${Math.round(item.duration)} ms</small></button>`).join('');
}
async function api(path, {method='GET', body, signal, raw=false} = {}) {
  const start = performance.now(); const form = body instanceof FormData;
  const requestBody = form ? { file:body.get('file')?.name, doc_type:body.get('doc_type'), metadata_json:body.get('metadata_json') } : body;
  const options = {method, signal, headers:{'X-Admin-API-Key':'admin:admin@123'}};
  if (state.sessionToken) options.headers['X-Session-Token'] = state.sessionToken;
  if (body !== undefined) { options.body = form ? body : JSON.stringify(body); if (!form) options.headers['Content-Type'] = 'application/json'; }
  let response, text, data;
  try {
    response = await fetch('/api' + path, options); text = await response.text();
    try { data = JSON.parse(text); } catch { data = text; }
    logRequest({method,path,status:response.status,duration:performance.now()-start,request:requestBody,response:data});
    if (!response.ok) { const error = new Error(typeof data?.detail === 'string' ? data.detail : `${response.status} ${response.statusText}: ${pretty(data?.detail || data)}`); error.status=response.status; throw error; }
    return raw ? {response, data, text, duration:performance.now()-start} : data;
  } catch (error) {
    if (!response) logRequest({method,path,status:error.name === 'AbortError' ? 'cancelled' : 'network error',duration:performance.now()-start,request:requestBody,response:error.message});
    throw error;
  }
}
async function run(task, button) {
  const old = button?.textContent;
  if (button) { button.disabled=true; button.textContent='Working…'; }
  try { return await task(); } catch (error) { if (error.name !== 'AbortError') toast(error.message, true); }
  finally { if (button) { button.disabled=false; button.textContent=old; } }
}
function objectValue(selector) {
  const text = $(selector).value.trim(); const value = text ? JSON.parse(text) : {};
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('Enter a JSON object, for example {"district":"chennai"}.');
  return value;
}
function statusBadge(status) {
  const color = status === 'active' || status === 'ok' ? 'success' : status === 'failed' || status === 'degraded' ? 'danger' : status === 'pending' || status === 'processing' ? 'warning' : 'neutral';
  return `<span class="badge ${color}">${escape(status)}</span>`;
}
async function waitForDocument(id, predicate, missingIsSuccess=false) {
  for(let attempt=0;attempt<15;attempt++) {
    try { const doc=await api('/documents/'+id); if(predicate(doc))return doc; }
    catch(error) { if(missingIsSuccess&&error.status===404)return null; if(error.status!==404)throw error; }
    await new Promise(resolve=>setTimeout(resolve,150));
  }
  throw new Error('The API accepted the change but it is not visible yet. Refresh the document list.');
}
function showView(view, updateHash = true) {
  if (!$(`#view-${view}`)) view='chat'; state.view=view;
  $$('.view').forEach(el => el.classList.toggle('active', el.id === 'view-'+view));
  $$('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('#page-label').textContent = $(`.nav-item[data-view="${view}"]`).textContent.trim().replace(/^[^A-Za-z]+/, '');
  if (updateHash) history.replaceState(null,'','#'+view);
  if (view === 'documents') run(loadDocuments);
  if (view === 'system') run(loadSystem);
  if (view === 'explorer' && !state.endpoints.length) run(loadSchema);
}
$$('.nav-item').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
window.addEventListener('hashchange', () => showView(location.hash.slice(1), false));

async function checkHealth() {
  if (state.healthBusy) return; state.healthBusy=true;
  try {
    const health = await api('/health');
    $('#health-badge').className = `badge ${health.status === 'ok' ? 'success' : 'warning'}`;
    $('#health-badge').textContent = health.status === 'ok' ? '● Docker backend online' : '● Backend degraded';
    const problems = Object.entries(health).filter(([key,value]) => !['status', 'version'].includes(key) && value !== 'ok');
    $('#global-banner').classList.toggle('hidden', !problems.length);
    if (problems.length) { $('#global-banner').className='banner'; $('#global-banner').textContent='Backend needs attention: '+problems.map(([key,value]) => `${key}: ${value}`).join(' · '); }
    return health;
  } catch (error) {
    $('#health-badge').className='badge danger'; $('#health-badge').textContent='● Backend offline';
    $('#global-banner').className='banner error'; $('#global-banner').textContent=error.message;
  } finally { state.healthBusy=false; }
}
$('#health-badge').addEventListener('click', checkHealth);

function updateSession(value) { $('#session-id').value=value; localStorage.setItem('nursingbot-local-session',value); }
function addMessage(role, text = '') {
  $('.chat-welcome')?.remove(); const element=document.createElement('article'); element.className=`message ${role}`;
  element.innerHTML=`<div class="message-avatar">${role === 'user' ? 'YOU' : 'n+'}</div><div class="message-content"><div class="message-label">${role === 'user' ? 'You' : 'NursingBot'}<time>${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time></div><div class="message-text"></div><div class="message-extras"></div></div>`;
  $('.message-text',element).textContent=text; $('#chat-messages').append(element); scrollChat(); return element;
}
function scrollChat() { $('#chat-messages').scrollTop=$('#chat-messages').scrollHeight; }
function decorateMessage(element, meta) {
  const extras=$('.message-extras',element); extras.innerHTML='';
  if (meta.is_grounded !== undefined) extras.insertAdjacentHTML('beforeend', `<div class="message-flags"><span class="badge ${meta.is_grounded ? 'success' : 'warning'}">${meta.is_grounded ? 'Citations validated' : 'Abstained / not grounded'}</span><span class="badge neutral">Retrieval gate ${meta.gate_passed ? 'passed' : 'did not pass'}</span></div>`);
  if (meta.abstention_reason) {const reason=document.createElement('p');reason.className='help';reason.textContent='Why: '+meta.abstention_reason;extras.append(reason);}
  if (meta.applied_filters && Object.keys(meta.applied_filters).length) {
    const filters=document.createElement('details'); filters.innerHTML='<summary>Applied filters</summary><pre></pre>'; $('pre',filters).textContent=pretty(meta.applied_filters); extras.append(filters);
  }
  if (meta.citations?.length) {
    const citations=document.createElement('div'); citations.className='citation-grid';
    citations.innerHTML=meta.citations.map(citation => `<button class="citation" data-document="${escape(citation.document_id)}"><strong>↗ Source ${escape(citation.source_index)}</strong>${escape(citation.metadata?.institution || citation.metadata?.doc_type || 'Official source')} · score ${escape(citation.score)}${citation.metadata?.page_number ? ' · page '+escape(citation.metadata.page_number) : ''}</button>`).join(''); extras.append(citations);
    const raw=document.createElement('details'); raw.innerHTML='<summary>Inspect citation metadata</summary><pre></pre>'; $('pre',raw).textContent=pretty(meta.citations); extras.append(raw);
  }
  scrollChat();
}
async function streamChat(payload, assistant) {
  const start=performance.now(); const events=[]; let response;
  try {
    const token = await ensureSession();
    const headers = {'Content-Type':'application/json','X-Admin-API-Key':'admin:admin@123'};
    if (token) headers['X-Session-Token'] = token;
    response=await fetch('/api/chat',{method:'POST',headers,body:JSON.stringify(payload),signal:state.chatAbort.signal});
    if (!response.ok) { const text=await response.text(); let data; try {data=JSON.parse(text);} catch {data=text;} logRequest({method:'POST',path:'/chat (SSE)',status:response.status,duration:performance.now()-start,request:payload,response:data}); throw new Error(pretty(data.detail || data)); }
    const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer='',answer='',meta={citations:[]},ended=false;
    function handleBlock(block) {
      let type='message'; const lines=[];
      for (const line of block.split('\n')) { if (line.startsWith('event:')) type=line.slice(6).trim(); else if (line.startsWith('data:')) lines.push(line.slice(5).trimStart()); }
      if (!lines.length) return;
      const data=JSON.parse(lines.join('\n')); events.push({event:type,data});
      if (type === 'token') { answer+=data.content || ''; $('.message-text',assistant).classList.remove('thinking'); $('.message-text',assistant).textContent=answer; scrollChat(); }
      if (type === 'citations') meta.citations=data;
      if (type === 'filters') meta.applied_filters=data;
      if (type === 'end') { meta={...meta,...data}; if (data.session_id) updateSession(data.session_id); ended=true; }
      if (type === 'error') throw new Error(pretty(data));
    }
    while (true) {
      const {value,done}=await reader.read(); if (done) break;
      buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');
      let boundary; while ((boundary=buffer.indexOf('\n\n')) >= 0) { const block=buffer.slice(0,boundary); buffer=buffer.slice(boundary+2); handleBlock(block); }
    }
    buffer+=decoder.decode(); if (buffer.trim()) handleBlock(buffer);
    if (!ended) throw new Error('The event stream ended before its final event. Inspect the API logs or retry.');
    decorateMessage(assistant,meta); logRequest({method:'POST',path:'/chat (SSE)',status:response.status,duration:performance.now()-start,request:payload,response:events});
  } catch (error) {
    if (!response?.ok) { if (!response) logRequest({method:'POST',path:'/chat (SSE)',status:error.name==='AbortError'?'cancelled':'error',duration:performance.now()-start,request:payload,response:error.message}); }
    else logRequest({method:'POST',path:'/chat (SSE)',status:error.name==='AbortError'?'cancelled':'stream error',duration:performance.now()-start,request:payload,response:{events,error:error.message}});
    throw error;
  }
}
$('#chat-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.chatAbort) return;
  const query=$('#chat-input').value.trim(); if (!query) return;
  let filters; try { filters=objectValue('#chat-filters'); } catch(error) {toast(error.message,true); return;}
  await ensureSession();
  const sessionId=$('#session-id').value.trim() || crypto.randomUUID(); updateSession(sessionId);
  addMessage('user',query); const assistant=addMessage('assistant','Searching sources and validating the answer'); $('.message-text',assistant).classList.add('thinking');
  const payload={query,session_id:sessionId,filters,stream:$('#stream-mode').checked};
  state.chatAbort=new AbortController(); $('#send-chat').disabled=true; $('#cancel-chat').classList.remove('hidden'); $('#chat-status').textContent='Waiting for the Docker backend…'; $('#chat-input').value='';
  const started=performance.now();
  try {
    if (payload.stream) await streamChat(payload,assistant);
    else { const result=await api('/chat',{method:'POST',body:payload,signal:state.chatAbort.signal}); updateSession(result.session_id); $('.message-text',assistant).textContent=result.answer; decorateMessage(assistant,result); }
    $('#chat-status').textContent=`Response completed in ${((performance.now()-started)/1000).toFixed(1)} seconds.`;
  } catch(error) { assistant.classList.add('error'); $('.message-text',assistant).textContent=error.name==='AbortError'?'Request stopped. The backend may finish its current operation.':error.message; $('#chat-status').textContent='Request did not complete. Inspect the request log for details.'; }
  finally { $('.message-text',assistant).classList.remove('thinking'); state.chatAbort=null; $('#send-chat').disabled=false; $('#cancel-chat').classList.add('hidden'); scrollChat(); }
});
$('#chat-input').addEventListener('keydown',event => { if (event.key==='Enter' && !event.shiftKey) {event.preventDefault(); $('#chat-form').requestSubmit();} });
$('#cancel-chat').addEventListener('click',()=>state.chatAbort?.abort());
$('#new-chat').addEventListener('click', async () => {
  if (state.chatAbort) { toast('Stop the current request first.'); return; }
  localStorage.removeItem('nursingbot-local-token');
  localStorage.removeItem('nursingbot-local-session');
  state.sessionToken = null;
  updateSession('');
  $('#chat-messages').innerHTML = '';
  $('#chat-status').textContent = 'New conversation ready.';
  await ensureSession();
  $('#chat-input').focus();
});
$('#copy-session').addEventListener('click',()=>run(async()=>{if(!$('#session-id').value) throw new Error('There is no session ID yet.');await navigator.clipboard.writeText($('#session-id').value);toast('Session ID copied.');}));
$('#load-conversation').addEventListener('click',event=>run(async()=>{
  if(state.chatAbort) throw new Error('Stop the current request first.');
  const id=$('#session-id').value.trim(); if(!id) throw new Error('Enter a session ID first.');
  const result=await api('/conversations/'+encodeURIComponent(id)); $('#chat-messages').innerHTML='';
  if(!result.messages.length) toast('This session has no saved messages.');
  for(const message of result.messages) {const element=addMessage(message.role,message.content);if(message.citations?.length) decorateMessage(element,{citations:message.citations});}
  updateSession(id); $('#chat-status').textContent=`Loaded ${result.messages.length} saved messages.`;
},event.currentTarget));
$('#delete-conversation').addEventListener('click',event=>run(async()=>{
  const id=$('#session-id').value.trim(); if(!id) throw new Error('Enter a session ID first.');
  if(!confirm('Delete all messages in this conversation?')) return;
  await api('/conversations/'+encodeURIComponent(id),{method:'DELETE'}); updateSession(''); $('#chat-messages').innerHTML='';toast('Conversation deleted.');
},event.currentTarget));
$$('[data-prompt]').forEach(button=>button.addEventListener('click',()=>{$('#chat-input').value=button.dataset.prompt;$('#chat-input').focus();}));

async function loadDocuments() {
  const pageSize = Number($('#doc-page-size').value) || 20;
  const params = new URLSearchParams({ page: state.docPage, page_size: pageSize });
  for (const [name, selector] of [['status', '#doc-status'], ['doc_type', '#doc-type-filter'], ['content_type', '#doc-content']]) {
    if ($(selector).value.trim()) params.set(name, $(selector).value.trim());
  }
  const result = await api('/documents?' + params);
  state.docTotal = result.total;
  const docs = result.documents || result.items || [];
  $('#document-count').textContent = `${result.total} document${result.total === 1 ? '' : 's'}`;
  $('#documents-table').innerHTML = docs.length ? docs.map(doc => `<tr><td><strong>${escape(doc.filename)}</strong><small>${escape(doc.doc_type)} · ${escape(doc.content_type)}</small>${doc.failure_reason ? `<small style="color:var(--danger)">${escape(doc.failure_reason.slice(0, 140))}</small>` : ''}</td><td>${statusBadge(doc.status)}</td><td>${doc.total_chunks}</td><td>${escape(new Date(doc.created_at).toLocaleDateString())}</td><td><button class="row-button" data-document="${doc.id}">Inspect ↗</button></td></tr>`).join('') : '<tr><td colspan="5" class="empty">No sources match these filters. Upload a document to begin.</td></tr>';
  const pages = Math.max(1, Math.ceil(result.total / pageSize));
  $('#doc-page-label').textContent = `Page ${state.docPage} of ${pages}`;
  $('#prev-doc-page').disabled = state.docPage <= 1;
  $('#next-doc-page').disabled = state.docPage >= pages;
}
$('#refresh-documents').addEventListener('click',event=>run(loadDocuments,event.currentTarget));
$('#apply-doc-filters').addEventListener('click',event=>{state.docPage=1;run(loadDocuments,event.currentTarget);});
$('#prev-doc-page').addEventListener('click',()=>{state.docPage=Math.max(1,state.docPage-1);run(loadDocuments);});
$('#next-doc-page').addEventListener('click',()=>{state.docPage++;run(loadDocuments);});
function updateFileLabel() {const file=$('#upload-file').files[0];$('#file-label').textContent=file?`${file.name} · ${(file.size/1024).toFixed(1)} KiB`:'Choose a file or drop it here';}
$('#upload-file').addEventListener('change',updateFileLabel);
for(const eventName of ['dragenter','dragover']) $('#drop-zone').addEventListener(eventName,event=>{event.preventDefault();$('#drop-zone').classList.add('dragging');});
for(const eventName of ['dragleave','drop']) $('#drop-zone').addEventListener(eventName,event=>{event.preventDefault();$('#drop-zone').classList.remove('dragging');});
$('#drop-zone').addEventListener('drop',event=>{if(event.dataTransfer.files.length){$('#upload-file').files=event.dataTransfer.files;updateFileLabel();}});
$('#upload-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{
  const file=$('#upload-file').files[0];if(!file) throw new Error('Choose a file.');if(file.size>50*1024*1024) throw new Error('This file exceeds 50 MiB.');
  const metadata=objectValue('#upload-metadata');const form=new FormData();form.append('file',file);form.append('doc_type',$('#upload-type').value.trim()||'general');form.append('metadata_json',JSON.stringify(metadata));
  const doc=await api('/documents/upload',{method:'POST',body:form});state.uploadWatch=doc.id;
  $('#upload-result').className='result-note';$('#upload-result').textContent=`${doc.filename}: ${doc.status}. Document ${doc.id}. Watching ingestion status…`;
  await loadDocuments();await openDocument(doc.id,false);toast('Source uploaded. The Docker worker will ingest it.');
},$('#upload-button'));});
async function loadChunks() {
  if(!state.selectedDoc)return;const chunks=await api(`/documents/${state.selectedDoc.id}/chunks?limit=10&offset=${state.chunkOffset}`);state.chunkCount=chunks.length;
  $('#chunks-label').textContent=chunks.length?`${state.chunkOffset+1}–${state.chunkOffset+chunks.length}`:'No chunks';
  $('#prev-chunks').disabled=state.chunkOffset===0;$('#next-chunks').disabled=chunks.length<10;
  $('#detail-chunks').innerHTML=chunks.length?chunks.map(chunk=>`<article class="chunk"><div class="chunk-header"><strong>Chunk ${chunk.chunk_index+1}${chunk.page_number?' · Page '+chunk.page_number:''}</strong>${statusBadge(chunk.status)}</div><p>${escape(chunk.content)}</p><details><summary>Context and metadata</summary><pre>${escape(pretty({id:chunk.id,contextualised_text:chunk.contextualised_text,metadata:chunk.metadata}))}</pre></details></article>`).join(''):'<div class="empty">No indexed chunks yet. Check the document state or refresh after ingestion.</div>';
}
async function openDocument(id,scroll=true,navigate=true) {
  if(navigate)showView('documents');const doc=await api('/documents/'+encodeURIComponent(id));state.selectedDoc=doc;state.chunkOffset=0;
  $('#document-detail').classList.remove('hidden');$('#detail-title').textContent=doc.filename;$('#detail-id').textContent=doc.id;$('#detail-record').textContent=pretty(doc);$('#detail-metadata').value=pretty(doc.metadata);await loadChunks();
  if(scroll) $('#document-detail').scrollIntoView({behavior:'smooth',block:'start'});
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-document]');if(button)run(()=>openDocument(button.dataset.document),button);});
$('#close-detail').addEventListener('click',()=>{state.selectedDoc=null;$('#document-detail').classList.add('hidden');});
$('#detail-status').addEventListener('click',event=>run(async()=>{const status=await api(`/documents/${state.selectedDoc.id}/status`);toast(`${status.status} · ${status.total_chunks} chunks${status.failure_reason?' · '+status.failure_reason:''}`);await openDocument(state.selectedDoc.id,false);},event.currentTarget));
$('#detail-chat').addEventListener('click',()=>{$('#chat-filters').value=pretty({document_id:state.selectedDoc.id});showView('chat');$('#chat-input').focus();toast('Chat is scoped to the selected source.');});
$('#metadata-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const result=await api(`/documents/${state.selectedDoc.id}`,{method:'PATCH',body:{metadata:objectValue('#detail-metadata')}});await waitForDocument(result.id,doc=>Object.entries(result.metadata).every(([key,value])=>JSON.stringify(doc.metadata[key])===JSON.stringify(value)));state.selectedDoc=result;$('#detail-record').textContent=pretty(result);await loadChunks();toast('Metadata updated.');},$('button',event.currentTarget));});
$('#retire-document').addEventListener('click',event=>run(async()=>{if(!confirm(`Retire "${state.selectedDoc.filename}"? It will be excluded from retrieval.`))return;await api('/documents/'+state.selectedDoc.id,{method:'DELETE'});await waitForDocument(state.selectedDoc.id,doc=>doc.status==='retired');await openDocument(state.selectedDoc.id,false);await loadDocuments();toast('Document retired.');},event.currentTarget));
$('#hard-delete-document').addEventListener('click',event=>run(async()=>{if(!confirm(`Permanently delete "${state.selectedDoc.filename}", its original object, and all chunks?`))return;await api('/documents/'+state.selectedDoc.id+'?hard=true',{method:'DELETE'});await waitForDocument(state.selectedDoc.id,()=>false,true);state.selectedDoc=null;$('#document-detail').classList.add('hidden');await loadDocuments();toast('Document permanently deleted.');},event.currentTarget));
$('#prev-chunks').addEventListener('click',()=>{state.chunkOffset=Math.max(0,state.chunkOffset-10);run(loadChunks);});
$('#next-chunks').addEventListener('click',()=>{state.chunkOffset+=10;run(loadChunks);});
let watchBusy=false;
async function watchUpload() {
  if(!state.uploadWatch||watchBusy)return;watchBusy=true;
  try {
    const status=await api(`/documents/${state.uploadWatch}/status`);$('#upload-result').className=`result-note${status.status==='failed'?' error':''}`;
    $('#upload-result').textContent=`Ingestion ${status.status} · ${status.total_chunks} chunks${status.failure_reason?' · '+status.failure_reason:''}`;
    if(['active','failed','retired'].includes(status.status)) { const id=state.uploadWatch;state.uploadWatch=null;await loadDocuments();if(state.selectedDoc?.id===id)await openDocument(id,false,false);toast(status.status==='active'?'Document is indexed and ready for chat.':'Ingestion '+status.status+'. Inspect its failure reason.',status.status==='failed'); }
  } catch(error) {if(error.status===404)state.uploadWatch=null;} finally {watchBusy=false;}
}

$('#retrieval-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{
  const params=new URLSearchParams({query:$('#retrieval-query').value,top_k:$('#retrieval-top-k').value});if($('#retrieval-district').value.trim())params.set('district',$('#retrieval-district').value.trim());
  const result=await api('/admin/retrieval-preview?'+params);$('#retrieval-summary').className='retrieval-summary';
  $('#retrieval-summary').innerHTML=`<span class="badge ${result.gate_passed?'success':'warning'}">Relevance gate ${result.gate_passed?'passed':'did not pass'}</span><span>Top RRF score <strong>${escape(result.top_score)}</strong></span><span>${result.items.length} retrieved chunks</span><span>Filters ${escape(JSON.stringify(result.applied_filters))}</span>`;
  $('#retrieval-results').innerHTML=result.items.length?result.items.map((item,index)=>`<article class="result-card"><div class="result-card-head"><strong class="result-rank">${String(index+1).padStart(2,'0')} · Retrieved evidence</strong><span class="result-score">RRF ${escape(item.score)} · Dense #${escape(item.dense_rank??'—')} · Sparse #${escape(item.sparse_rank??'—')}</span></div><p>${escape(item.content)}</p><div class="metadata-pills">${Object.entries(item.metadata).map(([key,value])=>`<span class="metadata-pill">${escape(key)}: ${escape(typeof value==='object'?JSON.stringify(value):value)}</span>`).join('')}</div><button class="row-button" data-document="${escape(item.document_id)}">Inspect source document ↗</button><small class="mono subtle-text">${escape(item.chunk_id)}</small></article>`).join(''):'<div class="empty-state"><h2>No matching evidence.</h2><p>Try different wording, remove the district filter, or check your active documents.</p></div>';
},$('button[type=submit]',event.currentTarget));});

async function loadSystem() {
  const results=await Promise.allSettled([api('/health'),api('/metrics'),api('/')]);
  const health=results[0].status==='fulfilled'?results[0].value:{status:'offline',error:results[0].reason.message};
  const metrics=results[1].status==='fulfilled'?results[1].value:{};
  $('#system-health').textContent=pretty({health,service:results[2].status==='fulfilled'?results[2].value:results[2].reason.message});
  $('#system-cards').innerHTML=[['API status',health.status,'Dependency checks'],['Active documents',metrics.active_documents??'—','Included in retrieval'],['Active chunks',metrics.active_chunks??'—','Indexed evidence'],['Ingestion','Queued','Docker worker']].map(([label,value,detail])=>`<div class="stat"><small>${escape(label)}</small><strong>${escape(value)}</strong><p>${escape(detail)}</p></div>`).join('');
}
$('#refresh-system').addEventListener('click',event=>run(loadSystem,event.currentTarget));
$('#reindex-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{
  const params=new URLSearchParams({new_model:$('#reindex-model').value.trim(),min_accuracy:$('#reindex-accuracy').value});$('#reindex-result').classList.remove('hidden');$('#reindex-result').textContent='Building and evaluating replacement vectors. This request may take several minutes…';
  try { const result=await api('/admin/reindex?'+params,{method:'POST'});$('#reindex-result').textContent=pretty(result);toast(result.success?'Reindex completed.':result.message,!result.success);await loadSystem(); }
  catch(error){$('#reindex-result').textContent=error.message;throw error;}
  finally{$('#reindex-confirm').checked=false;}
},$('button[type=submit]',event.currentTarget));});

async function loadSchema() {
  const schema=await api('/openapi.json');state.endpoints=[];
  for(const [path,operations] of Object.entries(schema.paths))for(const [method,operation]of Object.entries(operations))if(['get','post','patch','delete','put'].includes(method))state.endpoints.push({path,method:method.toUpperCase(),...operation});
  $('#endpoint-select').innerHTML=state.endpoints.map((op,index)=>`<option value="${index}">${escape(op.method+' '+op.path+' — '+(op.summary||''))}</option>`).join('');
  const initial=state.endpoints.findIndex(op=>op.path==='/health');$('#endpoint-select').value=initial;selectEndpoint();
}
function selectEndpoint() {
  const op=state.endpoints[Number($('#endpoint-select').value)];if(!op)return;
  let path=op.path.replace('{document_id}',state.selectedDoc?.id||'{document_id}').replace('{session_id}',$('#session-id').value||'{session_id}');
  const examples={
    '/admin/retrieval-preview':'?query=nursing%20registration&top_k=10',
    '/admin/reindex':'?new_model='+encodeURIComponent($('#reindex-model').value||'openai/text-embedding-3-small')+'&min_accuracy=0.8',
  };path+=examples[op.path]||'';
  $('#explorer-method').value=op.method;$('#explorer-path').value=path;$('#endpoint-help').textContent=op.description||op.summary||'Replace path placeholders with actual IDs. Query parameters go after ? in the URL.';
  $('#explorer-body').value=op.path==='/chat'?pretty({query:'What information is available about nursing registration?',filters:{},stream:false}):op.method==='PATCH'?pretty({metadata:{district:'chennai'}}):'';
  if(op.path==='/documents/upload') $('#endpoint-help').textContent='Multipart file upload: use the Documents panel to select a file, document type, and metadata.';
}
$('#endpoint-select').addEventListener('change',selectEndpoint);$('#reload-schema').addEventListener('click',event=>run(loadSchema,event.currentTarget));
$('#explorer-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{
  let path=$('#explorer-path').value.trim();if(!path.startsWith('/'))path='/'+path;if(/[{}]/.test(path))throw new Error('Replace the {document_id} or {session_id} placeholder before sending.');
  const method=$('#explorer-method').value;const options={method,raw:true};if($('#explorer-body').value.trim()&&!['GET','DELETE'].includes(method))options.body=JSON.parse($('#explorer-body').value);
  $('#explorer-response').textContent='Waiting for response…';const start=performance.now();
  try{const result=await api(path,options);$('#explorer-response').textContent=pretty(result.data);$('#explorer-response-meta').textContent=`${result.response.status} ${result.response.statusText} · ${Math.round(result.duration)} ms`;}
  catch(error){$('#explorer-response').textContent=pretty(state.logs[0]?.response||error.message);$('#explorer-response-meta').textContent=`${error.status||'Error'} · ${Math.round(performance.now()-start)} ms`;throw error;}
},$('button[type=submit]',event.currentTarget));});

$('#toggle-log').addEventListener('click',()=>$('#log-panel').classList.toggle('hidden'));
$('#close-log').addEventListener('click',()=>$('#log-panel').classList.add('hidden'));
$('#clear-log').addEventListener('click',()=>{state.logs=[];$('#log-list').innerHTML='';$('#log-detail').textContent='Request log cleared.';$('#log-count').textContent='0';});
$('#log-list').addEventListener('click',event=>{const button=event.target.closest('[data-log]');if(button)$('#log-detail').textContent=pretty(state.logs.find(item=>item.id===button.dataset.log));});

async function initialize() {
  $('#session-id').value=localStorage.getItem('nursingbot-local-session')||'';
  const host=window.location.hostname||'127.0.0.1';
  $$('a[href*="localhost:8000/docs"]').forEach(a=>a.href='/docs');
  $$('a[href*="localhost:9001"]').forEach(a=>a.href=`http://${host}:9001`);
  $$('a[href*="localhost:6006"]').forEach(a=>a.href=`http://${host}:6006`);
  try {const info=await fetch('/local/info').then(response=>response.json());$('#model-info').innerHTML=`<small>CHAT</small><strong>${escape(info.chatModel||'Configured in .env')}</strong><small>EMBEDDINGS</small><strong>${escape(info.embeddingModel||'Configured in .env')}</strong><small>Real backend · ${escape(info.mode||'Docker')}</small>`;$('#reindex-model').value=info.embeddingModel||'';}catch{$('#model-info').textContent='Model settings are supplied by your Docker backend.';}
  showView(location.hash.slice(1)||'chat',false);await ensureSession();await checkHealth();run(loadSchema);
}
initialize();setInterval(checkHealth,30000);setInterval(watchUpload,3000);

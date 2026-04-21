'use strict';

const API = 'http://localhost:5050/api';
const IPFS_GW = 'https://gateway.fxhash.xyz/ipfs/';
const PER_PAGE = 40;

const S = {
  user: null, projects: [], selected: new Set(),
  project: null, allObjkts: [], filtered: [], page: 1
};
let galleryViewMode = 'grid'; // 'grid' | 'list'

const ipfs = (uri) => uri ? uri.replace('ipfs://', IPFS_GW) : '';
const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString('en-US', {year:'numeric',month:'short',day:'numeric'}) : '—';
const fmtTez = (v) => v ? `${(parseInt(v)/1e6).toFixed(2)}` : '—';
const fmtAddr = (id, n) => n || (id ? id.slice(0,6)+'…'+id.slice(-4) : '—');
const escHtml = (str) => String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

// API
async function apiFetch(url) {
  const r = await fetch(url);
  if (!r.ok) { let m = `HTTP ${r.status}`; try { m = (await r.json()).error || m; } catch{} throw new Error(m); }
  return r.json();
}
async function apiPost(url, body) {
  const r = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
  if (!r.ok) { let m = `HTTP ${r.status}`; try { m = (await r.json()).error || m; } catch{} throw new Error(m); }
  return r;
}

function showLoad(msg) {
  document.getElementById('loading-msg').textContent = msg;
  document.getElementById('loading-overlay').classList.remove('hidden');
}
function hideLoad() { document.getElementById('loading-overlay').classList.add('hidden'); }

function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  
  if (id === 'view-home') {
    document.getElementById('site-header').style.display = 'none';
  } else {
    document.getElementById('site-header').style.display = 'flex';
  }
  
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function doSearch(queryValue) {
  let q = queryValue;
  if(typeof q !== 'string') {
    q = document.getElementById('home-input-wallet').value.trim() || document.getElementById('header-input-wallet').value.trim();
  }
  if (!q) return;
  
  document.getElementById('home-input-wallet').value = q;
  document.getElementById('header-input-wallet').value = q;
  
  showLoad('SEARCHING...');
  try {
    const data = await apiFetch(`${API}/search?q=${encodeURIComponent(q)}`);
    S.user = data.user;
    S.projects = data.projects || [];
    S.selected.clear();
    
    const url = new URL(window.location);
    url.searchParams.set('q', q);
    window.history.pushState({}, '', url);
    
    renderGallery();
    showView('view-gallery');
  } catch(e) {
    const errorMsg = "USUÁRIO OU ENDEREÇO NÃO ENCONTRADO";
    const hInput = document.getElementById('home-input-wallet');
    const hdrInput = document.getElementById('header-input-wallet');
    const oldH = hInput.value;
    const oldHdr = hdrInput.value;
    
    hInput.value = errorMsg;
    hdrInput.value = errorMsg;
    hInput.style.color = "red";
    hdrInput.style.color = "red";
    
    setTimeout(() => {
      hInput.value = oldH;
      hdrInput.value = oldHdr;
      hInput.style.color = "";
      hdrInput.style.color = "";
    }, 3000);
  } finally {
    hideLoad();
  }
}

function renderGallery() {
  document.getElementById('gallery-title').textContent = `${S.user.name||'Artist'} Projects`;
  document.getElementById('gallery-count').textContent = `${S.projects.length} ITEMS`;
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '';
  
  grid.className = galleryViewMode === 'list' ? 'grid list-view' : 'grid';
  
  S.projects.forEach(p => {
    const card = document.createElement('div');
    const isSel = S.selected.has(p.id);
    card.className = 'card' + (isSel ? ' selected' : '');
    const thumb = ipfs(p.thumbnailUri);
    const tags = (p.tags||[]).slice(0,1).join('').toUpperCase() || 'GENART';
    
    const btnHtml = `<button class="btn-select-overlay ${isSel?'selected':''}" data-id="${p.id}">${isSel ? 'SELECTED ✓' : 'SELECT'}</button>`;
    
    if (galleryViewMode === 'grid') {
      card.innerHTML = `
        <div class="card-image-wrap click-open">
          ${btnHtml}
          ${thumb ? `<img class="card-img" src="${thumb}" loading="lazy">` : ''}
        </div>
        <div class="card-info" style="flex-direction:column; gap:8px; width:100%;">
          <div style="display:flex; justify-content:space-between; width:100%; align-items:center;">
            <div class="card-title click-open" style="font-size:12px; font-weight:bold; color:var(--accent); cursor:pointer;" title="${escHtml(p.name)}">${escHtml(p.name)}</div>
            <div class="card-meta"><span>${p.objktsCount || p.supply || 0} ITEMS</span></div>
          </div>
          <div style="display:flex; justify-content:space-between; width:100%; color:var(--text-dim); font-size:10px;">
            <span>${fmtDate(p.createdAt)}</span>
            <span>${escHtml(tags)}</span>
          </div>
        </div>
      `;
    } else {
      card.innerHTML = `
        <div style="display:flex; align-items:center; width:100%; padding-right:15px;" class="list-card-inner">
           <div class="card-image-wrap click-open" style="flex-shrink:0; cursor:pointer;">
             ${thumb ? `<img class="card-img" src="${thumb}" loading="lazy">` : ''}
           </div>
           
           <div class="card-title click-open" style="flex:2; font-size:12px; font-weight:bold; color:var(--accent); cursor:pointer; padding:0 15px;" title="${escHtml(p.name)}">${escHtml(p.name)}</div>
           
           <div style="flex:1; color:var(--text-dim); font-size:10px;">${p.objktsCount || p.supply || 0} ITEMS</div>
           <div style="flex:1; color:var(--text-dim); font-size:10px;">${fmtDate(p.createdAt)}</div>
           
           <div style="flex-shrink:0;">${btnHtml}</div>
        </div>
      `;
    }
    
    card.querySelectorAll('.click-open').forEach(el => {
      el.onclick = () => openProject(p);
    });
    
    const selBtn = card.querySelector('.btn-select-overlay');
    selBtn.onclick = (e) => {
      e.stopPropagation();
      if (S.selected.has(p.id)) {
        S.selected.delete(p.id);
        selBtn.classList.remove('selected');
        selBtn.textContent = 'SELECT';
        card.classList.remove('selected');
      } else {
        S.selected.add(p.id);
        selBtn.classList.add('selected');
        selBtn.textContent = 'SELECTED ✓';
        card.classList.add('selected');
      }
      updateSelBtn();
    };
    grid.appendChild(card);
  });
  updateSelBtn();
}

function updateSelBtn() {
  const btn = document.getElementById('btn-backup-selected');
  btn.disabled = S.selected.size === 0;
  btn.textContent = `BACKUP ${S.selected.size} SELECTED`;
  
  document.getElementById('btn-view-grid').style.color = galleryViewMode==='grid' ? 'var(--bg)' : 'var(--text)';
  document.getElementById('btn-view-grid').style.background = galleryViewMode==='grid' ? 'var(--accent)' : 'transparent';
  document.getElementById('btn-view-list').style.color = galleryViewMode==='list' ? 'var(--bg)' : 'var(--text)';
  document.getElementById('btn-view-list').style.background = galleryViewMode==='list' ? 'var(--accent)' : 'transparent';
  
  const btnAll = document.getElementById('btn-select-all');
  if (S.selected.size > 0 && S.selected.size === S.projects.length) {
      btnAll.textContent = 'DESELECT ALL';
  } else {
      btnAll.textContent = 'SELECT ALL';
  }
}

async function openProject(proj) {
  S.project = proj; S.allObjkts = []; S.filtered = []; S.page = 1;
  document.getElementById('project-name').textContent = proj.name;
  
  renderProjectHeader(proj);
  showView('view-project');
  
  try {
    const data = await apiFetch(`${API}/project?id=${proj.id}`);
    if (data.project) { S.project = { ...proj, ...data.project }; renderProjectHeader(S.project); }
  } catch(e) {}
  loadObjkts();
}

function renderProjectHeader(p) {
  document.getElementById('project-cover').src = ipfs(p.displayUri || p.thumbnailUri) || '';
  document.getElementById('project-desc').textContent = p.metadata?.description || '';
  document.getElementById('project-supply').textContent = `${p.objktsCount??p.supply??0} ITEMS`;
  
  const price = p.pricingFixed ? fmtTez(p.pricingFixed.price) : (p.pricingDutchAuction ? fmtTez(p.pricingDutchAuction.restingPrice) : '—');
  document.getElementById('project-stats').innerHTML = `
    <span>SUPPLY: ${p.supply??'?'}</span>
    <span>MINTED: ${p.objktsCount??'?'}</span>
    <span>PRICE: ${price} ꜩ</span>
    <span>DATE: ${fmtDate(p.createdAt)}</span>
  `;
  const fxLink = document.getElementById('btn-fx-link');
  fxLink.href = `https://www.fxhash.xyz/generative/${p.slug||p.id}`;
}

async function loadObjkts() {
  const grid = document.getElementById('objkts-grid');
  grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:100px;color:var(--text-dim);"><div class="spinner"></div> RETRIEVING OBJKTS...</div>';
  let skip = 0, all = [];
  try {
    while(true) {
      const data = await apiFetch(`${API}/objkts?id=${S.project.id}&skip=${skip}`);
      const b = data.objkts; if(!b.length) break;
      all = all.concat(b); skip += 50; if(b.length < 50) break;
    }
    S.allObjkts = all; applyFilter();
  } catch(e) { grid.innerHTML = `<div style="grid-column:1/-1;color:red;">Error: ${e.message}</div>`; }
}

function applyFilter() {
  const q = (document.getElementById('objkts-search').value||'').toLowerCase();
  S.filtered = S.allObjkts.filter(o => {
    if(!q) return true;
    const s = `${o.name} ${o.owner?.name} ${o.owner?.id}`.toLowerCase();
    return s.includes(q);
  });
  S.filtered.sort((a,b) => (a.iteration||0)-(b.iteration||0));
  S.page = 1;
  renderObjktsPage();
}

function renderObjktsPage() {
  const grid = document.getElementById('objkts-grid');
  grid.innerHTML = '';
  const start = (S.page - 1) * PER_PAGE;
  const slice = S.filtered.slice(start, start + PER_PAGE);
  slice.forEach(o => {
    const card = document.createElement('div');
    card.className = 'card';
    const thumb = ipfs(o.displayUri || o.thumbnailUri);
    card.innerHTML = `
      <div class="card-image-wrap">
        ${thumb ? `<img class="card-img" src="${thumb}" loading="lazy">` : ''}
      </div>
      <div class="card-info">
        <div class="card-title">${escHtml(o.name || `Iteration #${o.iteration}`)}</div>
        <div class="card-meta"><span>#${o.iteration}</span> <span>${fmtTez(o.mintedPrice)} XT</span></div>
      </div>`;
    card.onclick = () => openPanel(o);
    grid.appendChild(card);
  });
  renderPagination();
}

function renderPagination() {
  const pag = document.getElementById('pagination');
  pag.innerHTML = '';
  const pages = Math.ceil(S.filtered.length / PER_PAGE);
  if(pages <= 1) return;
  for(let i=1; i<=pages; i++) {
    const b = document.createElement('button');
    b.textContent = i;
    if(i===S.page) b.classList.add('active-page');
    b.onclick = () => { S.page = i; renderObjktsPage(); };
    pag.appendChild(b);
  }
}

function openPanel(o) {
  document.getElementById('panel-img').src = ipfs(o.displayUri || o.thumbnailUri) || '';
  document.getElementById('panel-title').textContent = o.name || `Iteration #${o.iteration}`;
  
  const m = [
    ['Iteration', `#${o.iteration}`],
    ['Owner', `<a href="https://tzkt.io/${o.owner?.id}" target="_blank">${fmtAddr(o.owner?.id, o.owner?.name)}</a>`],
    ['Mint Price', `${fmtTez(o.mintedPrice)} ꜩ`],
    ['Last Sale', `${fmtTez(o.lastSoldPrice)} ꜩ`],
    ['Rarity', o.rarity ? `${(o.rarity*100).toFixed(2)}%` : '—'],
    ['Created', fmtDate(o.createdAt)]
  ];
  
  const traits = Array.isArray(o.features) ? o.features.map(f=>[f.name, f.value]) : [];
  if(traits.length) { m.push(['','']); m.push(['TRAITS / FEATURES','']); m.push(...traits); }
  
  document.getElementById('panel-info').innerHTML = m.map(r => 
    r[0] === '' ? `<div style="height:15px"></div>` :
    `<div class="panel-row"><div class="panel-key">${escHtml(String(r[0]))}</div><div class="panel-val">${r[1]}</div></div>`
  ).join('');
  
  document.getElementById('objkt-panel').classList.add('open');
  document.getElementById('panel-backdrop').classList.add('open');
}

function closePanel() {
  document.getElementById('objkt-panel').classList.remove('open');
  document.getElementById('panel-backdrop').classList.remove('open');
}

// BACKUP SYSTEM
let projectsToBackup = [];
let currentBackupTaskId = null;
function openBackupOpts() { 
  document.getElementById('backup-opts-modal').classList.remove('hidden'); 
  document.getElementById('btn-cancel-opts').onclick = () => {
    document.getElementById('backup-opts-modal').classList.add('hidden');
  };

  document.getElementById('btn-force-finish').onclick = async () => {
    if(!currentBackupTaskId) return;
    document.getElementById('btn-force-finish').innerHTML = "FORÇANDO...";
    try {
      await fetch(`${API}/backup/force_finish/${currentBackupTaskId}`, {method:"POST"});
    } catch(e) {
      console.error(e);
    }
  };
}

document.getElementById('btn-backup-selected').onclick = () => {
   projectsToBackup = S.projects.filter(p => S.selected.has(p.id));
   openBackupOpts();
};
document.getElementById('btn-backup-project').onclick = () => {
   projectsToBackup = [S.project];
   openBackupOpts();
};

let backupEventSource = null;
document.getElementById('btn-start-backup').onclick = async () => {
   document.getElementById('backup-opts-modal').classList.add('hidden');
   const options = {
     include_images: document.getElementById('chk-imgs').checked,
     include_source: document.getElementById('chk-src').checked,
     include_json: document.getElementById('chk-json').checked,
     include_csv: document.getElementById('chk-csv').checked,
   };
   
   document.getElementById('backup-prog-modal').classList.remove('hidden');
   const statusEl = document.getElementById('backup-status');
   const bar = document.getElementById('backup-bar');
   const btnDl = document.getElementById('btn-dl-backup');
   const btnClose = document.getElementById('btn-close-backup');
   const btnForce = document.getElementById('btn-force-finish');
   
   bar.style.width = '0%';
   btnDl.classList.add('hidden'); btnClose.classList.add('hidden');
   btnForce.classList.remove('hidden');
   btnForce.innerHTML = "FORÇAR CONCLUSÃO (.ZIP)";
   statusEl.innerHTML = '<div class="spinner"></div> INICIATING BACKUP ROUTINE...';
   
   try {
     const res = await apiPost(`${API}/backup/start`, { projects: projectsToBackup, options });
     const data = await res.json();
     if(data.error) throw new Error(data.error);
     
     currentBackupTaskId = data.task_id;
     if (backupEventSource) backupEventSource.close();
     backupEventSource = new EventSource(`${API}/backup/progress/${data.task_id}`);
     
     backupEventSource.onmessage = (e) => {
       const msg = JSON.parse(e.data);
       if(msg.error && msg.done) {
         statusEl.innerHTML = `FATAL EXCEPTION: ${msg.error}`;
         btnClose.classList.remove('hidden');
         btnForce.classList.add('hidden');
         backupEventSource.close();
         return;
       }
       
       bar.style.width = `${msg.progress}%`;
       statusEl.innerHTML = `<div>${escHtml(msg.current)}</div><div style="color:var(--text-dim);margin-top:5px;">${escHtml(msg.sub_msg)}</div>`;
       
       if(msg.done) {
         backupEventSource.close();
         if(msg.status === 'error') {
           statusEl.innerHTML += `<div style="color:red;margin-top:15px;">FAILED.</div>`;
         } else {
           btnDl.classList.remove('hidden');
           btnDl.onclick = () => window.location.href = `${API}/backup/download/${data.task_id}`;
         }
         btnClose.classList.remove('hidden');
       }
     };
   } catch(e) {
     statusEl.innerHTML = `ERROR: ${e.message}`;
     btnClose.classList.remove('hidden');
   }
};

document.getElementById('btn-close-backup').onclick = () => {
  document.getElementById('backup-prog-modal').classList.add('hidden');
};

document.getElementById('btn-dl-source').onclick = async () => {
  if(!S.project.generativeUri) return alert('No IPFS source URI found.');
  showLoad('CLONING IPFS REPOSITORY...');
  try {
    const r = await apiPost(`${API}/download/source`, { generativeUri: S.project.generativeUri, name: S.project.name });
    const blob = await r.blob();
    const a = Object.assign(document.createElement('a'), { href:URL.createObjectURL(blob), download:`${S.project.name.replace(/\W/g,'_')}_src.zip` });
    a.click();
  } catch(e){ alert(e.message); } finally { hideLoad(); }
};

// BOOT
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-view-grid').onclick = () => { galleryViewMode = 'grid'; renderGallery(); };
  document.getElementById('btn-view-list').onclick = () => { galleryViewMode = 'list'; renderGallery(); };
  document.getElementById('btn-select-all').onclick = () => {
    if (S.selected.size === S.projects.length) S.selected.clear();
    else S.projects.forEach(p => S.selected.add(p.id));
    renderGallery();
  };

  document.getElementById('home-btn-search').onclick = doSearch;
  document.getElementById('header-btn-search').onclick = doSearch;
  document.getElementById('home-input-wallet').onkeydown = (e) => { if(e.key==='Enter') doSearch(); };
  document.getElementById('header-input-wallet').onkeydown = (e) => { if(e.key==='Enter') doSearch(); };
  document.getElementById('nav-home').onclick = () => showView('view-home');
  document.getElementById('btn-back').onclick = () => showView('view-gallery');
  document.getElementById('objkts-search').oninput = applyFilter;
  document.getElementById('panel-close').onclick = closePanel;
  document.getElementById('panel-backdrop').onclick = closePanel;
  
  document.querySelectorAll('.quick-btn').forEach(b => {
    b.onclick = () => { doSearch(b.dataset.val); };
  });
  
  const urlParams = new URLSearchParams(window.location.search);
  const q = urlParams.get('q');
  if (q) doSearch(q);
});

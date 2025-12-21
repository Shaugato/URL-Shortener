function showToast(msg){
  const el = document.getElementById("toast");
  document.getElementById("toastBody").innerText = msg;
  const t = new bootstrap.Toast(el, { delay: 2500 });
  t.show();
}

async function shorten(){
  const url = document.getElementById("longUrl").value.trim();
  const alias = document.getElementById("alias").value.trim();
  const ttlHoursRaw = document.getElementById("ttlHours").value.trim();

  const body = { url };
  if(alias) body.alias = alias;
  if(ttlHoursRaw) body.ttlHours = Number(ttlHoursRaw);

  const res = await fetch("/api/shorten", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  const data = await res.json().catch(()=> ({}));
  if(!res.ok){
    showToast(data.error || `Error ${res.status}`);
    return;
  }

  document.getElementById("result").value = data.shortUrl || data.code;
  showToast("Short link created");
}

function copyResult(){
  const v = document.getElementById("result").value;
  if(!v) return showToast("Nothing to copy");
  navigator.clipboard.writeText(v);
  showToast("Copied!");
}

function clearAll(){
  ["longUrl","alias","ttlHours","result"].forEach(id => document.getElementById(id).value = "");
  showToast("Cleared");
}

document.getElementById("btnCreate").addEventListener("click", shorten);
document.getElementById("btnCopy").addEventListener("click", copyResult);
document.getElementById("btnClear").addEventListener("click", clearAll);

"""表达板 v2：家属操作+病人确认。六个标签覆盖 ICU 沟通标准能力集。"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["speak"])

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>表达板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f5f5f0;color:#1a1a1a;
display:flex;flex-direction:column;min-height:100vh;user-select:none}
.tabs{display:flex;gap:6px;padding:10px 8px 6px;background:#fff;border-bottom:1px solid #e5e5e0;flex-wrap:wrap}
.tabs button{flex:1;padding:10px 4px;border:1px solid #d5d5d0;border-radius:10px;background:#fff;
color:#666;font-size:14px;font-weight:600;min-height:44px;min-width:72px}
.tabs button.on{background:#1a1a1a;color:#fff;border-color:#1a1a1a}
.cats{display:flex;gap:6px;padding:8px 10px 4px;flex-wrap:wrap}
.cats button{padding:10px 18px;border:1px solid #d5d5d0;border-radius:10px;background:#fff;
color:#555;font-size:15px;font-weight:600;min-height:44px}
.cats button.on{background:#1a1a1a;color:#fff;border-color:#1a1a1a}
.grid{flex:1;display:grid;grid-template-columns:repeat(2,1fr);gap:10px;padding:10px 10px 20px;align-content:start}
.grid.g3{grid-template-columns:repeat(3,1fr)}
.btn{border:1px solid #d5d5d0;border-radius:14px;background:#fff;color:#1a1a1a;font-size:21px;
font-weight:600;padding:20px 8px;min-height:80px;cursor:pointer;line-height:1.3;
display:flex;align-items:center;justify-content:center;text-align:center}
.btn:active{background:#e8e8e3;transform:scale(.97)}
.yn .btn{font-size:36px;min-height:160px;font-weight:700}
.pain .btn{font-size:28px;min-height:72px}
.facebtn .btn{font-size:24px;min-height:90px}
.qhead{grid-column:1/3;text-align:center;padding:10px;font-size:19px;font-weight:700;line-height:1.6}
.qsub{font-weight:400;color:#888;font-size:14px}
.ynrow{grid-column:1/3;display:flex;gap:10px;margin-top:6px}
.ynrow .btn{flex:1;background:#1a1a1a;color:#fff;font-size:34px;min-height:110px;border:none}
#said{position:fixed;bottom:0;left:0;right:0;background:#1a1a1a;color:#fff;text-align:center;
padding:16px;font-size:21px;font-weight:700;z-index:9;transition:transform .2s;transform:translateY(100%)}
#said.show{transform:translateY(0)}
</style></head><body>
<div class="tabs">
  <button class="on" data-mode="board">需求板</button>
  <button data-mode="yn">是/否</button>
  <button data-mode="pain">疼痛分数</button>
  <button data-mode="faces">疼痛表情</button>
  <button data-mode="body">哪里不适</button>
  <button data-mode="talk">对话</button>
</div>
<div class="cats" id="cats"></div>
<div class="grid" id="grid"></div>
<div id="said"></div>
<script>
var BOARD = {"不舒服": ["疼痛", "呼吸费劲", "有痰", "恶心", "痒", "太冷", "太热", "嘴干"], "想要": ["湿润嘴唇", "翻身", "垫高", "擦脸", "大便", "小便", "想睡"], "叫人": ["叫护士", "叫医生", "叫家人"], "想说": ["谢谢", "辛苦了", "我爱你", "别担心", "想回家", "加油"]};
var FACES = [["😀", "不疼"], ["🙂", "有点疼"], ["😐", "不舒服"], ["🙁", "很疼"], ["😫", "非常疼"], ["😭", "剧痛"]];
var PARTS = ["头", "眼睛", "鼻子", "嘴唇", "喉咙", "胸口", "肚子", "背部", "腿", "左手", "右手", "小便处", "全身不适"];
var QUESTIONS = [["疼痛相关", "你疼吗"], ["口渴", "你想喝水吗"], ["体位", "你想翻身吗"], ["呼吸", "你吸得上气吗"], ["家属", "你想见家人吗"], ["小便", "你有尿意吗"]];
var pid = new URLSearchParams(location.search).get("person_id") || "2";
var cat = Object.keys(BOARD)[0], mode = "board", currentQ = "";

function say(text) {
  try {
    var u = new SpeechSynthesisUtterance(text);
    u.lang = "zh-CN"; u.rate = 0.9;
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  } catch(e) {}
  var el = document.getElementById("said");
  el.textContent = "\u2713 " + text;
  el.classList.add("show");
  setTimeout(function(){ el.classList.remove("show"); }, 2000);
  fetch("discussion", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person_id: +pid, author: "爸爸", category: "观察",
      content: "[表达板] " + text })
  }).catch(function(){});
}

function mbtn(label, sayVal, qVal) {
  var a = "";
  if (sayVal) a += " data-say='" + sayVal + "'";
  if (qVal) a += " data-q='" + qVal + "'";
  return "<button class='btn'" + a + ">" + label + "</button>";
}

function render() {
  var g = document.getElementById("grid"), c = document.getElementById("cats");
  if (mode === "yn") {
    c.innerHTML = ""; g.className = "grid yn";
    g.innerHTML = mbtn("是", "是") + mbtn("否", "不");
  } else if (mode === "pain") {
    c.innerHTML = ""; g.className = "grid g3 pain";
    var ph = "";
    for (var i = 0; i <= 10; i++) ph += mbtn(String(i), "疼痛 " + i + " 分");
    g.innerHTML = ph;
  } else if (mode === "faces") {
    c.innerHTML = ""; g.className = "grid g3 facebtn";
    var fh = "";
    for (var fi = 0; fi < FACES.length; fi++) {
      fh += mbtn(FACES[fi][0] + " " + FACES[fi][1], FACES[fi][0] + " " + FACES[fi][1]);
    }
    g.innerHTML = fh;
  } else if (mode === "body") {
    c.innerHTML = ""; g.className = "grid g3 pain";
    var bh = "";
    for (var bi = 0; bi < PARTS.length; bi++) {
      bh += mbtn(PARTS[bi], PARTS[bi] + "不适");
    }
    g.innerHTML = bh;
  } else if (mode === "talk") {
    c.innerHTML = ""; g.className = "grid";
    var th = "<div class='qhead' id='q'>家属先选一个话题，再让病人眨眼/点头回答</div>";
    for (var qi = 0; qi < QUESTIONS.length; qi++) {
      th += mbtn(QUESTIONS[qi][0], null, QUESTIONS[qi][1]);
    }
    th += "<div class='ynrow'>" +
      "<button class='btn' data-yn='y'>是</button>" +
      "<button class='btn' data-yn='n'>否</button></div>";
    g.innerHTML = th;
  } else {
    var ch = "";
    var keys = Object.keys(BOARD);
    for (var ki = 0; ki < keys.length; ki++) {
      ch += "<button data-cat='" + keys[ki] + "'" + (keys[ki] === cat ? " class='on'" : "") + ">" + keys[ki] + "</button>";
    }
    c.innerHTML = ch;
    g.className = "grid";
    var ih = "";
    var items = BOARD[cat];
    for (var ii = 0; ii < items.length; ii++) {
      ih += mbtn(items[ii], items[ii]);
    }
    g.innerHTML = ih;
  }
}

document.addEventListener("click", function(e) {
  var t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.say) { say(t.dataset.say); return; }
  if (t.dataset.q) {
    currentQ = t.dataset.q;
    document.getElementById("q").innerHTML = currentQ + "<br><span class='qsub'>让病人眨眼回答：一下=是，两下=否</span>";
    return;
  }
  if (t.dataset.yn) {
    say((currentQ || "问题") + (t.dataset.yn === "y" ? "：是" : "：否"));
    return;
  }
  if (t.dataset.cat) { cat = t.dataset.cat; render(); return; }
  if (t.closest(".tabs")) {
    mode = t.dataset.mode || "board";
    var btns = document.querySelectorAll(".tabs button");
    for (var i = 0; i < btns.length; i++) btns[i].classList.remove("on");
    t.classList.add("on");
    render();
  }
});

render();
</script></body></html>"""

@router.get("/speak", response_class=HTMLResponse)
def speak_page(person_id: int = 2):
    return PAGE

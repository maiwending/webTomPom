const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");
const roleEl = document.getElementById("role");
const statusEl = document.getElementById("status");

let role = "spectator";
let state = null;
let keys = { up: false, down: false };

function draw() {
  if (!state) {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    return;
  }

  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, state.width, state.height);

  ctx.fillStyle = "#fff";
  ctx.fillRect(10, state.left_y, state.paddle_w, state.paddle_h);
  ctx.fillRect(
    state.width - 10 - state.paddle_w,
    state.right_y,
    state.paddle_w,
    state.paddle_h
  );
  ctx.fillRect(state.ball_x, state.ball_y, state.ball_size, state.ball_size);

  ctx.font = "32px Consolas, Courier New, monospace";
  ctx.textAlign = "center";
  ctx.fillText(state.score_left, state.width / 4, 40);
  ctx.fillText(state.score_right, (state.width * 3) / 4, 40);

  if (state.game_over) {
    ctx.fillStyle = "#0f0";
    ctx.font = "32px Bahnschrift, Trebuchet MS, sans-serif";
    const winner =
      state.winner === "left" ? "LEFT WINS!" : "RIGHT WINS!";
    ctx.fillText(winner, state.width / 2, state.height / 2 - 20);
    ctx.font = "18px Bahnschrift, Trebuchet MS, sans-serif";
    ctx.fillText(
      "Press SPACE to restart",
      state.width / 2,
      state.height / 2 + 16
    );
  }
}

function sendInput(ws) {
  ws.send(
    JSON.stringify({
      type: "input",
      up: keys.up,
      down: keys.down,
    })
  );
}

function updateStatus(text) {
  statusEl.textContent = text;
}

function connect() {
  const host = window.location.hostname;
  const ws = new WebSocket(`ws://${host}:8765`);

  ws.addEventListener("open", () => {
    updateStatus("Connected");
  });

  ws.addEventListener("close", () => {
    updateStatus("Disconnected. Retrying...");
    setTimeout(connect, 1000);
  });

  ws.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "role") {
      role = msg.role;
      roleEl.textContent = `Role: ${role}`;
    } else if (msg.type === "state") {
      state = msg.state;
      draw();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.repeat) return;
    if (event.key === "w" || event.key === "W") keys.up = true;
    if (event.key === "s" || event.key === "S") keys.down = true;
    if (event.key === "ArrowUp") keys.up = true;
    if (event.key === "ArrowDown") keys.down = true;
    if (event.key === "+" || event.key === "=") {
      ws.send(JSON.stringify({ type: "speed", delta: 1 }));
    }
    if (event.key === "-" || event.key === "_") {
      ws.send(JSON.stringify({ type: "speed", delta: -1 }));
    }
    if (event.key === " ") {
      ws.send(JSON.stringify({ type: "reset" }));
    }
    sendInput(ws);
  });

  window.addEventListener("keyup", (event) => {
    if (event.key === "w" || event.key === "W") keys.up = false;
    if (event.key === "s" || event.key === "S") keys.down = false;
    if (event.key === "ArrowUp") keys.up = false;
    if (event.key === "ArrowDown") keys.down = false;
    sendInput(ws);
  });
}

connect();

import asyncio
import json
import math
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import requests
import websockets

WIDTH = 640
HEIGHT = 480
PADDLE_W = 10
PADDLE_H = 60
BALL_SIZE = 16
PADDLE_SPEED = 10
TARGET_FPS = 60
WIN_SCORE = 5
SPIN_STRENGTH = 0.15  # Adds curve based on paddle movement at impact
AI_ENDPOINT = os.getenv("TOMPOM_AI_ENDPOINT", "http://localhost:1234/v1/chat/completions")
AI_MODEL = os.getenv("TOMPOM_AI_MODEL", "meta-llama-3-8b-instruct")
AI_TIMEOUT = float(os.getenv("TOMPOM_AI_TIMEOUT", "0.8"))
AI_INTERVALS = {"easy": 0.25, "medium": 0.12, "hard": 0.06}
AI_DEADBAND = {"easy": 18, "medium": 10, "hard": 6}
AI_PADDLE_SPEED = {"easy": 6, "medium": 8, "hard": 10}


@dataclass
class PlayerState:
    y: float
    move: int = 0  # -1 up, 0 still, 1 down


@dataclass
class BallState:
    x: float
    y: float
    vx: float
    vy: float
    hit: bool = False


class GameState:
    def __init__(self) -> None:
        self.score_left = 0
        self.score_right = 0
        self.game_time = 0
        self.base_speed = 5.0
        self.rally_speed = 5.0
        self.rally_speed_locked = False
        self.left = PlayerState(y=(HEIGHT - PADDLE_H) / 2.0)
        self.right = PlayerState(y=(HEIGHT - PADDLE_H) / 2.0)
        self.ball = self._new_ball(angle=0.47, speed=self.base_speed)
        self.game_over = False
        self.winner = None

    def _new_ball(self, angle: float, speed: float) -> BallState:
        vx = speed * math.cos(angle)
        vy = speed * math.sin(angle)
        return BallState(
            x=(WIDTH - BALL_SIZE) / 2.0,
            y=(HEIGHT - BALL_SIZE) / 2.0,
            vx=vx,
            vy=vy,
        )

    def reset(self) -> None:
        self.score_left = 0
        self.score_right = 0
        self.game_time = 0
        self.base_speed = 5.0
        self.rally_speed = self.base_speed
        self.rally_speed_locked = False
        self.left.y = (HEIGHT - PADDLE_H) / 2.0
        self.right.y = (HEIGHT - PADDLE_H) / 2.0
        self.ball = self._new_ball(angle=0.47, speed=self.base_speed)
        self.game_over = False
        self.winner = None


class PongServer:
    def __init__(self) -> None:
        self.state = GameState()
        self.clients = set()
        self.roles = {}
        self.input_by_role = {"left": 0, "right": 0}
        self.lock = asyncio.Lock()
        self.ai_mode = os.getenv("TOMPOM_AI", "auto").lower()
        self.ai_difficulty = os.getenv("TOMPOM_AI_DIFFICULTY", "medium").lower()
        self.ai_interval = AI_INTERVALS.get(self.ai_difficulty, AI_INTERVALS["medium"])
        self.ai_deadband = AI_DEADBAND.get(self.ai_difficulty, AI_DEADBAND["medium"])
        self.ai_paddle_speed = AI_PADDLE_SPEED.get(
            self.ai_difficulty, AI_PADDLE_SPEED["medium"]
        )
        self.ai_role = None
        self.ai_target_y = {"left": None, "right": None}

    def _assign_role(self, ws):
        if "left" not in self.roles.values():
            role = "left"
        elif "right" not in self.roles.values():
            role = "right"
        else:
            role = "spectator"
        self.roles[ws] = role
        return role

    async def _send_role(self, ws, role):
        await ws.send(json.dumps({"type": "role", "role": role}))

    async def register(self, ws):
        self.clients.add(ws)
        role = self._assign_role(ws)
        self._update_ai_assignment()
        await self._send_role(ws, role)

    async def unregister(self, ws):
        self.clients.discard(ws)
        role = self.roles.pop(ws, None)
        if role in self.input_by_role:
            self.input_by_role[role] = 0
        self._update_ai_assignment()

    async def handle_message(self, ws, msg):
        role = self.roles.get(ws, "spectator")
        msg_type = msg.get("type")
        if msg_type == "input" and role in ("left", "right"):
            up = bool(msg.get("up"))
            down = bool(msg.get("down"))
            move = -1 if up and not down else 1 if down and not up else 0
            async with self.lock:
                self.input_by_role[role] = move
        elif msg_type == "reset":
            async with self.lock:
                if self.state.game_over:
                    self.state.reset()
        elif msg_type == "speed":
            delta = msg.get("delta")
            if isinstance(delta, (int, float)):
                async with self.lock:
                    self._adjust_speed(delta)

    async def ws_handler(self, ws):
        await self.register(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self.handle_message(ws, msg)
        finally:
            await self.unregister(ws)

    def _reflect_angle(self, ball_center_y, paddle_y, paddle_move):
        hit_pos = (ball_center_y - paddle_y) / PADDLE_H
        hit_pos = max(0.0, min(1.0, hit_pos))
        angle_range = math.pi / 3.0
        angle_offset = (hit_pos - 0.5) * 2 * angle_range
        # Add spin based on paddle movement (-1 up, 1 down)
        spin = -paddle_move * SPIN_STRENGTH
        return angle_offset + spin

    def _update_ai_assignment(self):
        if self.ai_mode == "off":
            self.ai_role = None
            return

        humans = {role for role in self.roles.values() if role in ("left", "right")}
        if self.ai_mode == "on":
            if "left" not in humans:
                self.ai_role = "left"
            elif "right" not in humans:
                self.ai_role = "right"
            else:
                self.ai_role = None
            return

        # Auto: use AI only when exactly one human is connected.
        if len(humans) == 1:
            self.ai_role = "right" if "left" in humans else "left"
        else:
            self.ai_role = None

    def _predict_ball_y(self, snapshot, target_x):
        bx = snapshot["ball_x"]
        by = snapshot["ball_y"]
        vx = snapshot["ball_vx"]
        vy = snapshot["ball_vy"]
        if vx == 0:
            return by

        t = (target_x - bx) / vx
        if t <= 0:
            return by

        projected = by + vy * t
        period = 2 * (HEIGHT - BALL_SIZE)
        y = projected % period
        if y > HEIGHT - BALL_SIZE:
            y = period - y
        return y

    def _ai_target_for(self, role, snapshot):
        if role == "left":
            paddle_x = PADDLE_W
        else:
            paddle_x = WIDTH - PADDLE_W - BALL_SIZE

        moving_toward = snapshot["ball_vx"] < 0 if role == "left" else snapshot["ball_vx"] > 0
        if moving_toward:
            target_y = self._predict_ball_y(snapshot, paddle_x) - PADDLE_H / 2.0
        else:
            target_y = (HEIGHT - PADDLE_H) / 2.0

        return max(0.0, min(HEIGHT - PADDLE_H, target_y))

    def _llm_move(self, role, snapshot):
        prompt = (
            "You control a Pong paddle. Reply with exactly one word: UP, DOWN, or STAY.\n"
            f"role={role}\n"
            f"paddle_y={snapshot['left_y'] if role == 'left' else snapshot['right_y']}\n"
            f"ball_x={snapshot['ball_x']}\n"
            f"ball_y={snapshot['ball_y']}\n"
            f"ball_vx={snapshot['ball_vx']}\n"
            f"ball_vy={snapshot['ball_vy']}\n"
            f"height={HEIGHT}\n"
        )
        payload = {
            "model": AI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only UP, DOWN, or STAY. No other text.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 4,
            "temperature": 0.2,
            "stream": False,
        }

        try:
            response = requests.post(AI_ENDPOINT, json=payload, timeout=AI_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None

        content = ""
        if "choices" in data and data["choices"]:
            content = data["choices"][0]["message"]["content"]
        elif "message" in data and "content" in data["message"]:
            content = data["message"]["content"]
        elif "messages" in data and data["messages"]:
            content = data["messages"][-1]["content"]

        match = re.search(r"\b(up|down|stay)\b", str(content), re.IGNORECASE)
        if not match:
            return None
        word = match.group(1).lower()
        if word == "up":
            return -1
        if word == "down":
            return 1
        return 0

    async def ai_loop(self):
        while True:
            await asyncio.sleep(self.ai_interval)
            async with self.lock:
                role = self.ai_role
                if not role or self.state.game_over:
                    continue
                snapshot = {
                    "left_y": self.state.left.y,
                    "right_y": self.state.right.y,
                    "ball_x": self.state.ball.x,
                    "ball_y": self.state.ball.y,
                    "ball_vx": self.state.ball.vx,
                    "ball_vy": self.state.ball.vy,
                }
                self.ai_target_y[role] = self._ai_target_for(role, snapshot)

    async def update(self):
        async with self.lock:
            if self.state.game_over:
                return

            self.state.game_time += 1
            self.state.base_speed = max(1.0, 5.0 + (self.state.game_time * 0.001))
            if not self.state.rally_speed_locked:
                self.state.rally_speed = self.state.base_speed

            self.state.left.move = self.input_by_role["left"]
            self.state.right.move = self.input_by_role["right"]

            # If AI controls a side, move paddle toward predicted intercept.
            if self.ai_role == "left":
                target = self.ai_target_y["left"]
                if target is not None:
                    diff = target - self.state.left.y
                    step = max(-self.ai_paddle_speed, min(self.ai_paddle_speed, diff))
                    self.state.left.y += step
                self.state.left.move = 0
            else:
                self.state.left.y += self.state.left.move * PADDLE_SPEED

            if self.ai_role == "right":
                target = self.ai_target_y["right"]
                if target is not None:
                    diff = target - self.state.right.y
                    step = max(-self.ai_paddle_speed, min(self.ai_paddle_speed, diff))
                    self.state.right.y += step
                self.state.right.move = 0
            else:
                self.state.right.y += self.state.right.move * PADDLE_SPEED
            self.state.left.y = max(0, min(HEIGHT - PADDLE_H, self.state.left.y))
            self.state.right.y = max(0, min(HEIGHT - PADDLE_H, self.state.right.y))

            ball = self.state.ball
            ball.x += ball.vx
            ball.y += ball.vy

            if ball.y <= 0 or ball.y + BALL_SIZE >= HEIGHT:
                ball.vy = -ball.vy

            # Paddle collision
            left_x = PADDLE_W
            right_x = WIDTH - PADDLE_W - BALL_SIZE
            ball_center_y = ball.y + BALL_SIZE / 2.0

            if ball.x <= left_x:
                if self.state.left.y - BALL_SIZE <= ball.y <= self.state.left.y + PADDLE_H:
                    if not ball.hit:
                        angle = self._reflect_angle(
                            ball_center_y, self.state.left.y, self.state.left.move
                        )
                        speed = self.state.rally_speed
                        ball.vx = speed * math.cos(angle)
                        ball.vy = speed * math.sin(angle)
                        ball.hit = True
                else:
                    self._score("left")
            elif ball.x >= right_x:
                if self.state.right.y - BALL_SIZE <= ball.y <= self.state.right.y + PADDLE_H:
                    if not ball.hit:
                        angle = math.pi + self._reflect_angle(
                            ball_center_y, self.state.right.y, self.state.right.move
                        )
                        speed = self.state.rally_speed
                        ball.vx = speed * math.cos(angle)
                        ball.vy = speed * math.sin(angle)
                        ball.hit = True
                else:
                    self._score("right")
            else:
                ball.hit = False

    def _score(self, side):
        if side == "left":
            self.state.score_right += 1
        else:
            self.state.score_left += 1

        if self.state.score_left >= WIN_SCORE:
            self.state.game_over = True
            self.state.winner = "left"
        elif self.state.score_right >= WIN_SCORE:
            self.state.game_over = True
            self.state.winner = "right"

        if not self.state.game_over:
            spread = math.pi / 6.0
            jitter = random.uniform(-spread / 2.0, spread / 2.0)
            base_angle = math.pi if side == "left" else 0.0
            self.state.rally_speed_locked = False
            self.state.rally_speed = self.state.base_speed
            speed = self.state.rally_speed
            self.state.ball = self.state._new_ball(base_angle + jitter, speed)

    def _adjust_speed(self, delta):
        start_speed = (
            self.state.rally_speed if self.state.rally_speed_locked else self.state.base_speed
        )
        new_speed = max(1.0, min(15.0, start_speed + delta))
        if new_speed == start_speed:
            return
        self.state.rally_speed = new_speed
        self.state.rally_speed_locked = True
        ball = self.state.ball
        magnitude = math.hypot(ball.vx, ball.vy)
        if magnitude > 0:
            scale = new_speed / magnitude
            ball.vx *= scale
            ball.vy *= scale

    async def broadcast_state(self):
        state = {
            "type": "state",
            "state": {
                "width": WIDTH,
                "height": HEIGHT,
                "paddle_w": PADDLE_W,
                "paddle_h": PADDLE_H,
                "ball_size": BALL_SIZE,
                "left_y": self.state.left.y,
                "right_y": self.state.right.y,
                "ball_x": self.state.ball.x,
                "ball_y": self.state.ball.y,
                "score_left": self.state.score_left,
                "score_right": self.state.score_right,
                "game_over": self.state.game_over,
                "winner": self.state.winner,
            },
        }
        if not self.clients:
            return
        msg = json.dumps(state)
        await asyncio.gather(*(ws.send(msg) for ws in list(self.clients)), return_exceptions=True)

    async def game_loop(self):
        tick = 1.0 / TARGET_FPS
        while True:
            start = time.perf_counter()
            await self.update()
            await self.broadcast_state()
            elapsed = time.perf_counter() - start
            await asyncio.sleep(max(0.0, tick - elapsed))


def serve_http(root_dir: str, port: int) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=root_dir)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


async def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    web_root = os.path.join(base_dir, "web")

    serve_http(web_root, 8000)

    server = PongServer()
    async with websockets.serve(server.ws_handler, "0.0.0.0", 8765):
        asyncio.create_task(server.ai_loop())
        await server.game_loop()


if __name__ == "__main__":
    asyncio.run(main())

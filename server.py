import asyncio
import json
import math
import os
import random
import threading
import time
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import websockets

WIDTH = 640
HEIGHT = 480
PADDLE_W = 10
PADDLE_H = 60
BALL_SIZE = 16
PADDLE_SPEED = 10
TARGET_FPS = 60
WIN_SCORE = 5


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
        await self._send_role(ws, role)

    async def unregister(self, ws):
        self.clients.discard(ws)
        role = self.roles.pop(ws, None)
        if role in self.input_by_role:
            self.input_by_role[role] = 0

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

    def _reflect_angle(self, ball_center_y, paddle_y):
        hit_pos = (ball_center_y - paddle_y) / PADDLE_H
        hit_pos = max(0.0, min(1.0, hit_pos))
        angle_range = math.pi / 3.0
        angle_offset = (hit_pos - 0.5) * 2 * angle_range
        return angle_offset

    async def update(self):
        async with self.lock:
            if self.state.game_over:
                return

            self.state.game_time += 1
            self.state.base_speed = max(1.0, 5.0 + (self.state.game_time * 0.001))

            self.state.left.move = self.input_by_role["left"]
            self.state.right.move = self.input_by_role["right"]

            self.state.left.y += self.state.left.move * PADDLE_SPEED
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
                        angle = self._reflect_angle(ball_center_y, self.state.left.y)
                        speed = self.state.base_speed
                        ball.vx = speed * math.cos(angle)
                        ball.vy = speed * math.sin(angle)
                        ball.hit = True
                else:
                    self._score("left")
            elif ball.x >= right_x:
                if self.state.right.y - BALL_SIZE <= ball.y <= self.state.right.y + PADDLE_H:
                    if not ball.hit:
                        angle = math.pi + self._reflect_angle(ball_center_y, self.state.right.y)
                        speed = self.state.base_speed
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
            speed = self.state.base_speed
            self.state.ball = self.state._new_ball(base_angle + jitter, speed)

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
        await server.game_loop()


if __name__ == "__main__":
    asyncio.run(main())

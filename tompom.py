#
# Tom's Pong
# A simple pong game with realistic physics and AI
# http://tomchance.org.uk/projects/pong
#
# Released under the GNU General Public License

VERSION = "0.4"

# Scores for left (player1) and right (player2)
score1 = 0
score2 = 0

# Game time and difficulty
game_time = 0  # Elapsed time in frames
base_speed = 1  # Starting ball speed

try:
    import sys
    import random
    import math
    import os
    import getopt
    import pygame
    from socket import *
    from pygame.locals import *
except Exception as err:
    print(f"couldn't load module. {err}")
    sys.exit(2)

def reset_game():
    """Reset game state to initial values."""
    global score1, score2, game_time
    score1 = 0
    score2 = 0
    game_time = 0

def load_png(name):
    """ Load image and return image object"""
    # Prefer the data directory relative to this script, not the current working directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    fullname = os.path.join(script_dir, "data", name)
    try:
        image = pygame.image.load(fullname)
        # get_alpha is a method; call it. If surface has no per-pixel alpha use convert()
        if image.get_alpha() is None:
            image = image.convert()
        else:
            image = image.convert_alpha()
    except Exception as err:
        # If the file can't be found or loaded, make a simple placeholder surface so the
        # game can still run. This is helpful when launching the script from a different
        # working directory (e.g. from an editor) or if the data files are missing.
        print(f"Cannot load image: {fullname} ({err}) — using placeholder")
        # Create reasonable defaults based on filename
        name_lower = name.lower()
        if "bat" in name_lower:
            size = (10, 60)
            color = (255, 255, 255)
        elif "ball" in name_lower:
            size = (16, 16)
            color = (255, 255, 255)
        else:
            size = (32, 32)
            color = (200, 200, 200)

        # Ensure pygame is initialized before creating Surfaces
        try:
            image = pygame.Surface(size, pygame.SRCALPHA)
            image.fill(color)
        except Exception:
            # As a last resort, raise so the caller can see a meaningful error
            raise SystemExit(f"Failed to create placeholder surface for {name}")

    return image, image.get_rect()

class Ball(pygame.sprite.Sprite):
    """A ball that will move across the screen
    Returns: ball object
    Functions: update, calcnewpos
    Attributes: area, vector"""

    def __init__(self, xy, vector):
        pygame.sprite.Sprite.__init__(self)
        self.image, self.rect = load_png("ball.png")
        screen = pygame.display.get_surface()
        self.area = screen.get_rect()
        self.vector = vector
        self.hit = 0
        # position the ball at the requested coordinates
        try:
            self.rect.topleft = xy
        except Exception:
            # fall back to (0,0) if invalid
            self.rect.topleft = (0, 0)

    def update(self):
        newpos = self.calcnewpos(self.rect,self.vector)
        self.rect = newpos
        (angle,z) = self.vector

        if not self.area.contains(newpos):
            tl = not self.area.collidepoint(newpos.topleft)
            tr = not self.area.collidepoint(newpos.topright)
            bl = not self.area.collidepoint(newpos.bottomleft)
            br = not self.area.collidepoint(newpos.bottomright)
            if tr and tl or (br and bl):
                angle = -angle
            if tl and bl:
                # Ball left the left edge -> left player missed
                if self.offcourt('left'):
                    return True  # Someone won
                return
            if tr and br:
                # Ball left the right edge -> right player missed
                if self.offcourt('right'):
                    return True  # Someone won
                return
        else:
            # Deflate the rectangles so you can't catch a ball behind the bat
            p1rect = player1.rect.inflate(-3, -3)
            p2rect = player2.rect.inflate(-3, -3)

            # Do ball and bat collide?
            # Note: set self.hit when they collide and unset on the next iteration to avoid
            # repeated collisions while the ball is still overlapping the bat.
            if self.rect.colliderect(p1rect) and not self.hit:
                # Ball hit left bat; reflect angle based on where it hit
                angle = self._reflect_angle(self.rect, p1rect, is_left=True)
                self.hit = True
            elif self.rect.colliderect(p2rect) and not self.hit:
                # Ball hit right bat; reflect angle based on where it hit
                angle = self._reflect_angle(self.rect, p2rect, is_left=False)
                self.hit = True
            elif self.hit:
                self.hit = False
        self.vector = (angle,z)

    def calcnewpos(self,rect,vector):
        (angle,z) = vector
        (dx,dy) = (z*math.cos(angle),z*math.sin(angle))
        return rect.move(dx,dy)

    def _reflect_angle(self, ball_rect, bat_rect, is_left):
        """Calculate reflection angle based on where the ball hits the bat.

        ball_rect: the ball's rect
        bat_rect: the bat's deflated rect
        is_left: True if left bat, False if right bat

        Returns the new angle. Top of bat gives upward angle, bottom gives downward.
        """
        # Determine where on the bat the ball hit (0 = top, 1 = bottom)
        bat_top = bat_rect.top
        bat_height = bat_rect.height
        ball_center_y = ball_rect.centery
        hit_position = (ball_center_y - bat_top) / bat_height
        hit_position = max(0, min(1, hit_position))  # Clamp to [0, 1]

        # Map hit position to a reflection angle
        # Top of bat (hit_position ≈ 0) → steep upward angle
        # Middle of bat (hit_position ≈ 0.5) → shallow angle (near horizontal)
        # Bottom of bat (hit_position ≈ 1) → steep downward angle
        angle_range = math.pi / 3.0  # ±60 degrees from horizontal
        angle_offset = (hit_position - 0.5) * 2 * angle_range

        if is_left:
            # Left bat: reflect to the right, with upward/downward tilt
            new_angle = angle_offset
        else:
            # Right bat: reflect to the left, with upward/downward tilt
            new_angle = math.pi + angle_offset

        return new_angle

    def offcourt(self, side):
        """Handle ball leaving the court on left or right side.

        side: 'left' if ball went off left edge, 'right' if off right edge.
        Increments score, recenters the ball and serves toward the side that missed.
        Speed increases gradually over time.
        
        Returns True if someone has won (score >= 5), False otherwise.
        """
        global score1, score2, game_time, base_speed

        # If ball went off left, right player scores; vice versa.
        if side == 'left':
            score2 += 1
        else:
            score1 += 1

        # Print score so user can see it in terminal (helpful during development)
        try:
            elapsed_sec = game_time / 60.0  # Convert frames to seconds at 60 FPS
            print(f"Score -- Left: {score1}  Right: {score2}  |  Time: {elapsed_sec:.1f}s  Speed: {base_speed:.2f}")
        except Exception:
            pass

        # Check for win condition (first to 5)
        if score1 >= 5 or score2 >= 5:
            return True

        # Reset ball to centre
        try:
            self.rect.center = self.area.center
        except Exception:
            pass

        # Serve toward the side that missed (so the scoring player sends the ball there)
        spread = math.pi / 6.0
        jitter = random.uniform(-spread/2, spread/2)
        if side == 'left':
            base = math.pi
        else:
            base = 0.0
        new_angle = base + jitter

        # Gradually increase speed over time: base_speed + 0.001 * elapsed_frames
        # This means speed increases by ~1 unit per ~15 seconds of gameplay
        try:
            z = base_speed + (game_time * 0.001)
            z = max(1, z)  # Ensure minimum speed
        except Exception:
            z = base_speed

        self.vector = (new_angle, z)
        self.hit = False
        return False

class Bat(pygame.sprite.Sprite):
    """Movable tennis 'bat' with which one hits the ball
    Returns: bat object
    Functions: reinit, update, moveup, movedown
    Attributes: which, speed"""

    def __init__(self, side):
        pygame.sprite.Sprite.__init__(self)
        self.image, self.rect = load_png("bat.png")
        screen = pygame.display.get_surface()
        self.area = screen.get_rect()
        self.side = side
        self.speed = 5
        self.state = "still"
        self.reinit()

    def reinit(self):
        self.state = "still"
        self.movepos = [0,0]
        if self.side == "left":
            self.rect.midleft = self.area.midleft
        elif self.side == "right":
            self.rect.midright = self.area.midright

    def update(self):
        newpos = self.rect.move(self.movepos)
        if self.area.contains(newpos):
            self.rect = newpos
        pygame.event.pump()

    def moveup(self):
        self.movepos[1] = self.movepos[1] - (self.speed)
        self.state = "moveup"

    def movedown(self):
        self.movepos[1] = self.movepos[1] + (self.speed)
        self.state = "movedown"


def main():
    # Initialise screen
    pygame.init()
    screen = pygame.display.set_mode((640, 480))
    pygame.display.set_caption("Basic Pong")
    # Font for score display
    try:
        pygame.font.init()
        font = pygame.font.Font(None, 48)
        small_font = pygame.font.Font(None, 24)
    except Exception:
        font = None
        small_font = None

    # Fill background
    background = pygame.Surface(screen.get_size())
    background = background.convert()
    background.fill((0, 0, 0))

    # Initialise players
    global player1
    global player2
    global score1, score2, game_time, base_speed
    player1 = Bat("left")
    player2 = Bat("right")

    # Initialise ball
    game_time = 0
    base_speed = 5
    ball = Ball((0,0),(0.47, base_speed))

    # Initialise sprites
    playersprites = pygame.sprite.RenderPlain((player1, player2))
    ballsprite = pygame.sprite.RenderPlain(ball)

    # Blit everything to the screen
    screen.blit(background, (0, 0))
    pygame.display.flip()

    # Initialise clock
    clock = pygame.time.Clock()

    # Event loop
    while True:
        # Make sure game doesn't run at more than 60 frames per second
        clock.tick(60)
        game_time += 1  # Increment elapsed time each frame

        for event in pygame.event.get():
            if event.type == QUIT:
                return
            elif event.type == KEYDOWN:
                if event.key == K_a:
                    player1.moveup()
                if event.key == K_z:
                    player1.movedown()
                if event.key == K_UP:
                    player2.moveup()
                if event.key == K_DOWN:
                    player2.movedown()
            elif event.type == KEYUP:
                if event.key == K_a or event.key == K_z:
                    player1.movepos = [0,0]
                    player1.state = "still"
                if event.key == K_UP or event.key == K_DOWN:
                    player2.movepos = [0,0]
                    player2.state = "still"

        # Clear previous ball/bat and score areas
        screen.blit(background, ball.rect, ball.rect)
        screen.blit(background, player1.rect, player1.rect)
        screen.blit(background, player2.rect, player2.rect)
        # Clear top area where scores are rendered
        try:
            score_area = pygame.Rect(0, 0, screen.get_width(), 64)
            screen.blit(background, (0, 0), score_area)
        except Exception:
            pass
        
        # Check for game-over condition from ball update
        game_over = ball.update()
        if game_over:
            # Determine winner before restart loop
            left_wins = score1 >= 5
            
            # Display winner and wait for key to restart
            ballsprite.draw(screen)
            playersprites.draw(screen)
            
            # Draw winner message
            if font is not None:
                try:
                    if left_wins:
                        winner_text = font.render("LEFT WINS!", True, (0, 255, 0))
                    else:
                        winner_text = font.render("RIGHT WINS!", True, (0, 255, 0))
                    tw = winner_text.get_width()
                    sw = screen.get_width()
                    sh = screen.get_height()
                    screen.blit(winner_text, (sw//2 - tw//2, sh//2 - 30))
                    
                    # Draw restart instruction
                    if small_font is not None:
                        restart_text = small_font.render("Press SPACE to play again", True, (200, 200, 200))
                        rw = restart_text.get_width()
                        screen.blit(restart_text, (sw//2 - rw//2, sh//2 + 20))
                except Exception:
                    pass
            pygame.display.flip()
            
            # Wait for spacebar to restart
            waiting = True
            while waiting:
                for event in pygame.event.get():
                    if event.type == QUIT:
                        return
                    elif event.type == KEYDOWN:
                        if event.key == K_SPACE:
                            # Reset game state
                            reset_game()
                            ball = Ball((0,0),(0.47, base_speed))
                            ballsprite = pygame.sprite.RenderPlain(ball)
                            player1.reinit()
                            player2.reinit()
                            # Clear screen for fresh game start
                            screen.blit(background, (0, 0))
                            pygame.display.flip()
                            waiting = False
                pygame.time.delay(50)
            continue
        
        ballsprite.update()
        playersprites.update()
        ballsprite.draw(screen)
        playersprites.draw(screen)
        # Draw scores
        if font is not None:
            try:
                left_text = font.render(str(score1), True, (255, 255, 255))
                right_text = font.render(str(score2), True, (255, 255, 255))
                lw = left_text.get_width()
                rw = right_text.get_width()
                sw = screen.get_width()
                screen.blit(left_text, (sw//4 - lw//2, 8))
                screen.blit(right_text, (3*sw//4 - rw//2, 8))
            except Exception:
                pass
        pygame.display.flip()


if __name__ == "__main__":
    main()
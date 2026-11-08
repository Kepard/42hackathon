package ai.holospeed.client;

import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.gui.screen.GameMenuScreen;
import net.minecraft.text.Text;
import org.lwjgl.glfw.GLFW;

/**
 * A real singleplayer pause that leaves the rendered world and HUD unobscured.
 * Extending the vanilla pause menu preserves the existing guarded arm and
 * world-lifecycle checks. Nothing here freezes ticks or changes server state:
 * Minecraft applies its normal integrated-server pause semantics.
 */
public final class HoloPauseScreen extends GameMenuScreen {
    public HoloPauseScreen() { super(false); }

    @Override protected void init() {
        // No menu widgets or title over the live camera's final game frame.
    }

    @Override public void render(DrawContext context, int mouseX, int mouseY, float delta) {
        // The game renderer already drew the world and HUD beneath this screen.
    }

    @Override public boolean shouldPause() { return true; }

    @Override public Text getNarratedTitle() {
        return Text.literal("Game paused. Press Escape for the game menu, or F9 to resume HoloSpeed.");
    }

    @Override public boolean keyPressed(int keyCode, int scanCode, int modifiers) {
        if (keyCode == GLFW.GLFW_KEY_ESCAPE && client != null) {
            // Escape reveals normal controls without briefly unpausing the world.
            client.setScreen(new GameMenuScreen(true));
            return true;
        }
        return super.keyPressed(keyCode, scanCode, modifiers);
    }
}

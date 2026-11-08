package ai.holospeed.client;

import net.minecraft.client.gui.screen.GameMenuScreen;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class HoloPauseScreenTest {
    @Test void remainsAVanillaPauseMenuForArmAndWorldLifecycleChecks() {
        HoloPauseScreen screen = new HoloPauseScreen();
        assertInstanceOf(GameMenuScreen.class, screen);
        assertTrue(screen.shouldPause());
        assertFalse(screen.shouldShowMenu());
    }

    @Test void addsNoWidgetsAndDoesNotDrawOverTheWorld() {
        HoloPauseScreen screen = new HoloPauseScreen();
        // With no initialized renderer or graphics context, neither path may
        // create vanilla menu widgets, draw a title, darken, or blur the world.
        assertDoesNotThrow(() -> screen.init());
        assertTrue(screen.children().isEmpty());
        assertDoesNotThrow(() -> screen.render(null, 0, 0, 0));
        assertDoesNotThrow(() -> screen.renderBackground(null, 0, 0, 0));
    }
}

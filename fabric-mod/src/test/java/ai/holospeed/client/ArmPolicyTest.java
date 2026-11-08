package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ArmPolicyTest {
    @Test void gameplayAndTheNormalPauseMenuMayResume() {
        for (var screen : new ArmPolicy.ScreenKind[]{ArmPolicy.ScreenKind.NONE, ArmPolicy.ScreenKind.PAUSE_MENU}) {
            assertNull(ArmPolicy.rejection(true, true, true, screen, true, true));
        }
    }

    @Test void OnlyAnEmptyCraftingScreenMayBeClosedAutomatically() {
        assertNull(ArmPolicy.rejection(true, true, true, ArmPolicy.ScreenKind.CRAFTING, true, true));
        for (boolean cursorEmpty : new boolean[]{false, true}) {
            for (boolean gridEmpty : new boolean[]{false, true}) {
                if (!cursorEmpty || !gridEmpty) assertNotNull(
                    ArmPolicy.rejection(true, true, true, ArmPolicy.ScreenKind.CRAFTING, cursorEmpty, gridEmpty));
            }
        }
        assertNotNull(ArmPolicy.rejection(true, true, true, ArmPolicy.ScreenKind.OTHER, true, true));
    }

    @Test void MissingWorldDeathOrFocusAlwaysRejectsEvenSafeScreens() {
        for (var screen : ArmPolicy.ScreenKind.values()) {
            assertNotNull(ArmPolicy.rejection(false, true, true, screen, true, true));
            assertNotNull(ArmPolicy.rejection(true, false, true, screen, true, true));
            assertNotNull(ArmPolicy.rejection(true, true, false, screen, true, true));
        }
    }
}

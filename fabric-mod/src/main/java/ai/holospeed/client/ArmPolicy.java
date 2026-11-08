package ai.holospeed.client;

/** Operator-start policy, separate from autonomous gameplay skills. */
final class ArmPolicy {
    enum ScreenKind { NONE, PAUSE_MENU, CRAFTING, OTHER }

    static String rejection(boolean worldReady, boolean alive, boolean focused,
                            ScreenKind screen, boolean cursorEmpty, boolean gridEmpty) {
        if (!worldReady) return "enter a world before starting";
        if (!alive) return "player must be alive before starting";
        if (!focused) return "focus the Minecraft window before starting";
        if (screen == ScreenKind.OTHER) return "close the unrelated game screen before starting";
        if (screen == ScreenKind.CRAFTING && (!cursorEmpty || !gridEmpty)) {
            return "empty the crafting grid and cursor before starting";
        }
        return null;
    }
}

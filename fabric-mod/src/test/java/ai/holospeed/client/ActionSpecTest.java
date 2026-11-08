package ai.holospeed.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

class ActionSpecTest {
    private static JsonObject json(String source) {
        return JsonParser.parseString(source).getAsJsonObject();
    }

    @Test
    void simultaneousMovementAndMiningPreserveTheEntireInputState() {
        ActionSpec action = ActionSpec.parse(json("""
                {"keys":["forward","sprint","jump","attack"],"duration_ticks":12,
                 "yaw_delta":-21.5,"pitch_delta":12,"hotbar":3,"scroll":-1}
                """));

        assertAll(
                () -> assertEquals(Set.of("forward", "sprint", "jump", "attack"), action.keys()),
                () -> assertEquals(12, action.duration()),
                () -> assertEquals(-21.5f, action.yaw()),
                () -> assertEquals(12f, action.pitch()),
                () -> assertEquals(Integer.valueOf(3), action.hotbar()),
                () -> assertEquals(-1, action.scroll()),
                () -> assertNull(action.cursor())
        );
    }

    @Test
    void anEmptyActionIsABoundedIdleRatherThanAHeldPreviousInput() {
        ActionSpec action = ActionSpec.parse(json("{}"));
        assertAll(
                () -> assertTrue(action.keys().isEmpty()),
                () -> assertEquals(6, action.duration()),
                () -> assertEquals(0f, action.yaw()),
                () -> assertEquals(0f, action.pitch()),
                () -> assertNull(action.hotbar()),
                () -> assertNull(action.cursor()),
                () -> assertEquals(0, action.scroll())
        );
    }

    @Test
    void unsafeAndFractionalDurationsCannotReachTheExecutor() {
        for (double duration : new double[]{-1, 0, 21, 100000, 1.5}) {
            JsonObject action = new JsonObject();
            action.addProperty("duration_ticks", duration);
            assertThrows(IllegalArgumentException.class, () -> ActionSpec.parse(action),
                    "duration_ticks=" + duration);
        }
        assertEquals(1, ActionSpec.parse(json("{\"duration_ticks\":1}")).duration());
        assertEquals(20, ActionSpec.parse(json("{\"duration_ticks\":20}")).duration());
    }

    @Test
    void stationaryMiningMayHoldAttackLongEnoughToBreakALog() {
        ActionSpec mining = ActionSpec.parse(json("{\"keys\":[\"attack\"],\"duration_ticks\":80}"));
        assertEquals(80, mining.duration());
        assertEquals(Set.of("attack"), mining.keys());
        assertThrows(IllegalArgumentException.class,
                () -> ActionSpec.parse(json("{\"keys\":[\"attack\"],\"duration_ticks\":81}")));
    }

    @Test
    void extendedMiningCannotAlsoMoveAimClickOrSwitchTools() {
        for (String source : new String[]{
                "{\"keys\":[\"forward\",\"attack\"],\"duration_ticks\":40}",
                "{\"keys\":[\"forward\",\"attack\"],\"duration_ticks\":81}",
                "{\"keys\":[\"attack\"],\"duration_ticks\":40,\"yaw_delta\":1}",
                "{\"keys\":[\"attack\"],\"duration_ticks\":40,\"pitch_delta\":1}",
                "{\"keys\":[\"attack\"],\"duration_ticks\":40,\"hotbar\":2}",
                "{\"keys\":[\"attack\"],\"duration_ticks\":40,\"scroll\":1}",
                "{\"keys\":[\"attack\"],\"duration_ticks\":40,\"cursor\":{\"x\":0.5,\"y\":0.5,\"button\":\"left\"}}"}) {
            assertThrows(IllegalArgumentException.class, () -> ActionSpec.parse(json(source)), source);
        }
    }

    @Test
    void nonFiniteNumbersCannotPoisonCameraOrInputDeadlines() {
        for (String field : new String[]{"yaw_delta", "pitch_delta", "duration_ticks", "hotbar", "scroll"}) {
            for (double value : new double[]{Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY}) {
                JsonObject action = new JsonObject();
                action.addProperty(field, value);
                assertThrows(IllegalArgumentException.class, () -> ActionSpec.parse(action),
                        field + "=" + value);
            }
        }
    }

    @Test
    void cameraBoundsPermitExactLimitsButRejectOvershoot() {
        ActionSpec limits = ActionSpec.parse(json("{\"yaw_delta\":-90,\"pitch_delta\":60}"));
        assertEquals(-90f, limits.yaw());
        assertEquals(60f, limits.pitch());
        for (String source : new String[]{
                "{\"yaw_delta\":90.001}", "{\"yaw_delta\":-90.001}",
                "{\"pitch_delta\":60.001}", "{\"pitch_delta\":-60.001}"}) {
            assertThrows(IllegalArgumentException.class, () -> ActionSpec.parse(json(source)), source);
        }
    }

    @Test
    void cursorCoordinatesUseNormalizedImageSpaceAndAllSupportedButtons() {
        String[] names = {"left", "right", "middle"};
        for (int button = 0; button < names.length; button++) {
            ActionSpec action = ActionSpec.parse(json(
                    "{\"cursor\":{\"x\":0,\"y\":1,\"button\":\"" + names[button] + "\"}}"));
            assertNotNull(action.cursor());
            assertEquals(0, action.cursor().x());
            assertEquals(1, action.cursor().y());
            assertEquals(button, action.cursor().button());
        }
        ActionSpec centered = ActionSpec.parse(json(
                "{\"cursor\":{\"x\":0.5,\"y\":0.25,\"button\":\"left\"}}"));
        assertEquals(0.5, centered.cursor().x());
        assertEquals(0.25, centered.cursor().y());
    }

    @Test
    void outOfBoundsOrNonFiniteCursorCannotClickOutsideTheGameImage() {
        for (String axis : new String[]{"x", "y"}) {
            for (double value : new double[]{-0.001, 1.001, 500, Double.NaN, Double.POSITIVE_INFINITY}) {
                JsonObject action = json("{\"cursor\":{\"x\":0.5,\"y\":0.5,\"button\":\"left\"}}");
                action.getAsJsonObject("cursor").addProperty(axis, value);
                assertThrows(IllegalArgumentException.class, () -> ActionSpec.parse(action),
                        axis + "=" + value);
            }
        }
    }

    @Test
    void incompleteCursorAndUnsupportedMouseButtonAreRejected() {
        for (String source : new String[]{
                "{\"cursor\":{\"y\":0.5,\"button\":\"left\"}}",
                "{\"cursor\":{\"x\":0.5,\"button\":\"left\"}}",
                "{\"cursor\":{\"x\":0.5,\"y\":0.5,\"button\":\"double\"}}"}) {
            assertThrows(IllegalArgumentException.class, () -> ActionSpec.parse(json(source)), source);
        }
    }

    @Test
    void unknownDuplicateAndOpposingKeysAreRejectedBeforeExecution() {
        for (String source : new String[]{
                "{\"keys\":[\"chat\"]}",
                "{\"keys\":[\"forward\",\"forward\"]}",
                "{\"keys\":[\"forward\",\"back\"]}",
                "{\"keys\":[\"right\",\"left\"]}"}) {
            assertThrows(IllegalArgumentException.class, () -> ActionSpec.parse(json(source)), source);
        }
    }

    @Test
    void unsupportedFieldsCannotSmuggleAlternativeControlInstructions() {
        assertThrows(IllegalArgumentException.class,
                () -> ActionSpec.parse(json("{\"command\":\"/give @s diamond\"}")));
        assertThrows(IllegalArgumentException.class,
                () -> ActionSpec.parse(json(
                        "{\"cursor\":{\"x\":0.5,\"y\":0.5,\"button\":\"left\",\"repeat\":99}}")));
    }

    @Test
    void numericStringsAndFractionalSlotsAreNotSilentlyCoerced() {
        for (String source : new String[]{
                "{\"duration_ticks\":\"20\"}", "{\"yaw_delta\":\"0\"}",
                "{\"hotbar\":1.5}", "{\"hotbar\":0}", "{\"hotbar\":10}",
                "{\"scroll\":0.5}", "{\"scroll\":10}", "{\"scroll\":-10}"}) {
            assertThrows(IllegalArgumentException.class, () -> ActionSpec.parse(json(source)), source);
        }
    }

    @Test
    void nullOptionalControlsAreAcceptedAndParsedInputStateIsImmutable() {
        ActionSpec action = ActionSpec.parse(json(
                "{\"keys\":[\"attack\"],\"hotbar\":null,\"cursor\":null}"));
        assertNull(action.hotbar());
        assertNull(action.cursor());
        assertThrows(UnsupportedOperationException.class, () -> action.keys().add("use"));
    }
}

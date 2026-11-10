package dev.observatory.mixin;

import dev.observatory.Observatory;
import net.minecraft.client.Keyboard;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(Keyboard.class)
public class KeyboardMixin {
    @Inject(method = "onChar", at = @At("HEAD"), cancellable = true)
    private void freezeText(long window, int codepoint, int mods, CallbackInfo ci) {
        if (Observatory.isPaused()) ci.cancel();
    }
    @Inject(method = "onKey", at = @At("HEAD"), cancellable = true)
    private void freezeInput(long window, int key, int scanCode, int action, int modifiers, CallbackInfo ci) {
        if (Observatory.isPaused()) ci.cancel();
    }
}

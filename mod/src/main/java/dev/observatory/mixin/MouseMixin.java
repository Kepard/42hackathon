package dev.observatory.mixin;

import dev.observatory.Observatory;
import net.minecraft.client.Mouse;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(Mouse.class)
public class MouseMixin {
    @Inject(method = "onMouseScroll", at = @At("HEAD"), cancellable = true)
    private void freezeScroll(long window, double horizontal, double vertical, CallbackInfo ci) {
        if (Observatory.isPaused()) ci.cancel();
    }
    @Inject(method = "updateMouse", at = @At("HEAD"), cancellable = true)
    private void freezeLook(CallbackInfo ci) { if (Observatory.isPaused()) ci.cancel(); }

    @Inject(method = "onMouseButton", at = @At("HEAD"), cancellable = true)
    private void freezeButton(long window, int button, int action, int mods, CallbackInfo ci) {
        if (Observatory.isPaused()) ci.cancel();
    }
}

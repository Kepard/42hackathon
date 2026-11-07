package dev.observatory.mixin;

import dev.observatory.Observatory;
import net.minecraft.client.render.GameRenderer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(GameRenderer.class)
public class FrameMixin {
    @Inject(method = "render", at = @At("TAIL"))
    private void endFrame(float tickDelta, long startTime, boolean tick, CallbackInfo ci) {
        Observatory.endFrame();
    }
}

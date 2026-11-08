package ai.holospeed.client.mixin;

import ai.holospeed.client.HoloSpeedClient;
import net.minecraft.client.util.InputUtil;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(InputUtil.class)
public abstract class InputUtilMixin {
    @Inject(method = "isKeyPressed", at = @At("HEAD"), cancellable = true)
    private static void holospeed$heldModifier(long window, int code, CallbackInfoReturnable<Boolean> cir) {
        if (HoloSpeedClient.syntheticModifier(code)) cir.setReturnValue(true);
    }
}

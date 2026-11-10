package dev.observatory.mixin;

import dev.observatory.Observatory;
import net.minecraft.client.network.ClientPlayerEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ClientPlayerEntity.class)
public class PlayerMixin {
    @Inject(method = "tickMovement", at = @At("HEAD"), cancellable = true)
    private void freezeMovement(CallbackInfo ci) {
        if (Observatory.isPaused()) ci.cancel();
    }
}

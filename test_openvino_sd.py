from optimum.intel import OVStableDiffusionPipeline
import openvino as ov
import time

MODEL_ID = "OpenVINO/stable-diffusion-v1-5-int8-ov"

print("OpenVINO devices:", ov.Core().available_devices)
print("Loading Stable Diffusion model...")

pipe = OVStableDiffusionPipeline.from_pretrained(
    MODEL_ID,
    compile=False,
    safety_checker=None,
)

print("Compiling on Intel GPU...")
pipe.reshape(1, 512, 512)
pipe.compile()

prompt = (
    "a cinematic futuristic city at night, "
    "neon lights, realistic architecture, detailed, "
    "high quality digital art"
)

print("Generating image...")
start = time.time()

result = pipe(
    prompt,
    num_inference_steps=10,
    guidance_scale=7.5,
)

elapsed = time.time() - start

output = "openvino_test.png"
result.images[0].save(output)

print(f"Image saved to: {output}")
print(f"Generation time: {elapsed:.2f} seconds")
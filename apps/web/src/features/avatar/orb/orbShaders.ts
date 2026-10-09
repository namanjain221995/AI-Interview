/**
 * GLSL for the interviewer presence orb.
 *
 *   sphere vertex    smooth 3D-noise displacement (two low octaves, plus a fine
 *                    ripple layer while the candidate speaks); the normal is
 *                    rebuilt from the displaced surface (finite differences)
 *   sphere fragment  domain-warped inner colour flow over a 4-stop palette,
 *                    twisted by the swirl, glassy depth toward the core,
 *                    fresnel rim, a luminous core that swells with the voice,
 *                    a soft specular highlight and a gentle highlight shoulder
 *   halo             soft glow around the silhouette plus up to four ripple
 *                    rings, premultiplied for a transparent canvas
 *
 * Simplex noise: Ian McEwan / Ashima Arts "webgl-noise" formulation (MIT).
 */

export const NOISE_GLSL = /* glsl */ `
vec3 orb_mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 orb_mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 orb_permute(vec4 x) { return orb_mod289(((x * 34.0) + 10.0) * x); }
vec4 orb_taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = orb_mod289(i);
  vec4 p = orb_permute(orb_permute(orb_permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = orb_taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x;
  p1 *= norm.y;
  p2 *= norm.z;
  p3 *= norm.w;
  vec4 m = max(0.5 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 105.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
`;

export const ORB_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uDisplace;
uniform float uRipple;
varying vec3 vObj;
varying vec3 vNormalV;
varying vec3 vViewPos;
${NOISE_GLSL}

float orbDisplace(vec3 p) {
  float d = snoise(p * 0.95 + vec3(0.0, uTime * 0.21, uTime * 0.12)) * 0.7
          + snoise(p * 1.9 - vec3(uTime * 0.29, 0.0, uTime * 0.16)) * 0.3;
  float r = uRipple > 0.0 ? snoise(p * 2.4 + vec3(uTime * 1.5, -uTime * 1.0, 0.0)) * uRipple : 0.0;
  return d * uDisplace + r;
}

void main() {
  vec3 p = normalize(position);
  vec3 up = abs(p.y) < 0.98 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
  vec3 t = normalize(cross(up, p));
  vec3 b = cross(p, t);
  float e = 0.01;
  vec3 p0 = p * (1.0 + orbDisplace(p));
  vec3 pt = normalize(p + t * e);
  pt *= 1.0 + orbDisplace(pt);
  vec3 pb = normalize(p + b * e);
  pb *= 1.0 + orbDisplace(pb);
  vec3 n = normalize(cross(pt - p0, pb - p0));
  if (dot(n, p) < 0.0) n = -n;
  vObj = p;
  vec4 mv = modelViewMatrix * vec4(p0, 1.0);
  vViewPos = mv.xyz;
  vNormalV = normalize(normalMatrix * n);
  gl_Position = projectionMatrix * mv;
}
`;

export const ORB_FRAGMENT = /* glsl */ `
uniform float uFlowPhase;
uniform float uSwirl;
uniform float uWarmth;
uniform float uViolet;
uniform float uSweep;
uniform float uBrightness;
uniform float uSaturation;
uniform float uShimmer;
uniform float uEnergy;
uniform float uVoice;
uniform vec3 uCamObj;
uniform vec3 uFlow0;
uniform vec3 uFlow1;
uniform vec3 uFlow2;
uniform vec3 uFlow3;
uniform vec3 uCore;
uniform vec3 uRim;
uniform vec3 uWarm;
uniform vec3 uCool;
uniform vec3 uThink;
varying vec3 vObj;
varying vec3 vNormalV;
varying vec3 vViewPos;
${NOISE_GLSL}

/** Swirl: twist about the vertical axis (thinking), slowly turning. */
vec3 orbTwist(vec3 p) {
  float a = uFlowPhase * (0.12 + 0.55 * uSwirl) + p.y * uSwirl * 1.4;
  float ca = cos(a);
  float sa = sin(a);
  p.xz = mat2(ca, -sa, sa, ca) * p.xz;
  return p;
}

/**
 * Colour of the light at a (warped) point: a soft-max of three broad fields
 * over the base colour, so the orb shows distinct, saturated masses of colour
 * with soft borders (a single gradient ramp lands on muddy midpoints).
 * Returns rgb and, in w, how luminous the point is.
 */
vec4 orbLight(vec3 q) {
  float t = uFlowPhase;
  float sweep = uSweep * 0.6;
  // A finer octave gives the borders between colours some liquid structure.
  vec3 qd = q * 2.3 + vec3(0.0, t * 0.11, 0.0);
  float c = snoise(q * 0.87 + vec3(-2.3, 4.1, t * 0.052)) + 0.3 * snoise(qd + vec3(1.7, 0.0, 4.4)) - sweep;
  float d = snoise(q * 0.93 + vec3(6.1, -3.3, -t * 0.058)) + 0.3 * snoise(qd + vec3(-3.1, 2.2, 0.0));
  float a = snoise(q + vec3(0.0, 0.0, t * 0.071)) + 0.3 * snoise(qd + vec3(5.5, -1.2, 2.0)) + sweep;
  float b = snoise(q * 1.13 + vec3(3.7, -t * 0.063, 1.9)) + 0.3 * snoise(qd + vec3(0.4, 6.6, -2.5));
  const float K = 5.2;
  float w0 = exp(K * c);
  float w1 = exp(K * (d + 0.06));
  float w2 = exp(K * a);
  float w3 = exp(K * (b - 0.2));
  float sum = w0 + w1 + w2 + w3;
  vec3 col = (uFlow0 * w0 + uFlow1 * w1 + uFlow2 * w2 + uFlow3 * w3) / sum;
  // The accents carry the light; the deep and base zones stay rich and darker.
  float lum = (0.5 * w0 + 0.78 * w1 + 1.12 * w2 + 1.22 * w3) / sum * (1.0 + 0.3 * uVoice);
  return vec4(col, lum);
}

vec3 orbTint(vec3 c) {
  return c / max(dot(c, vec3(0.2126, 0.7152, 0.0722)), 1e-3);
}

void main() {
  vec3 N = normalize(vNormalV);
  vec3 V = normalize(-vViewPos);
  float ndv = clamp(dot(N, V), 0.0, 1.0);

  // The view ray through the glass (object space).
  vec3 ro = vObj;
  vec3 rd = normalize(vObj - uCamObj);
  float chord = max(0.0, -2.0 * dot(ro, rd));

  // One slow domain warp, shared by the depth samples: broad, liquid shapes.
  float t = uFlowPhase;
  vec3 pe = orbTwist(ro) * 0.6;
  vec3 warp = vec3(
    snoise(pe + vec3(0.0, t * 0.13, 0.0)),
    snoise(pe + vec3(5.2, 1.3, t * 0.11)),
    snoise(pe + vec3(2.7, t * 0.09, 7.1))) * (0.38 + 0.14 * uSwirl);

  // Two layers of light front to back over the deep core: soft depth.
  vec3 acc = vec3(0.0);
  float T = 1.0;
  for (int k = 0; k < 2; k++) {
    float s = chord * (0.04 + 0.16 * float(k));
    vec3 q = orbTwist(ro + rd * s) * 0.58 + warp + vec3(0.0, uVoice * 0.12, 0.0);
    vec4 l = orbLight(q);
    float a = k == 0 ? 0.72 : 0.7;
    acc += T * a * l.rgb * l.w;
    T *= 1.0 - a;
  }
  vec3 col = acc + T * uCore;

  // State leans (warm while speaking, cool while listening, violet while thinking).
  // Leans are tints (luminance-normalised), so they shift the hue without washing it out.
  col *= mix(vec3(1.0), orbTint(uWarm), max(uWarmth, 0.0) * 0.3);
  col *= mix(vec3(1.0), orbTint(uCool), max(-uWarmth, 0.0) * 0.3);
  col = mix(col * mix(vec3(1.0), orbTint(uThink), uViolet * 0.35), uThink * (0.3 + 0.7 * dot(col, vec3(0.33))), uViolet * 0.18);
  // Vibrance: blending the layers greys the colours a little; win the chroma back.
  float l0 = dot(col, vec3(0.2126, 0.7152, 0.0722));
  col = max(vec3(0.0), vec3(l0) + (col - vec3(l0)) * 1.15);

  // Body: luminous through the middle, thinner light toward the edge (volume).
  col *= 0.6 + 0.5 * pow(ndv, 0.65);
  // A luminous heart that swells with the voice (a gain, so the colours keep their hue).
  float heart = exp(-(1.0 - ndv * ndv) * 2.6);
  col *= 1.0 + heart * (0.06 + 0.4 * uVoice + 0.1 * uEnergy);
  // Gently lit from above.
  col *= 0.88 + 0.2 * (0.5 + 0.5 * N.y);

  // Glass: a tinted fresnel rim and a fine edge line.
  float f = 1.0 - ndv;
  vec3 rimTint = mix(uRim, acc / max(0.001, dot(acc, vec3(0.33))) * 0.45, 0.5);
  col += rimTint * (pow(f, 4.0) * (0.3 + 0.3 * uEnergy));
  col += uRim * pow(f, 14.0) * 0.55;
  // Key light (upper left): a broad soft sheen and a small, soft-edged glint.
  vec3 L = normalize(vec3(-0.45, 0.62, 0.64));
  float nh = max(dot(N, normalize(L + V)), 0.0);
  col += vec3(1.0, 0.985, 0.97) * (pow(nh, 18.0) * 0.06 + pow(nh, 260.0) * 0.6);
  // A faint bounce from below right.
  vec3 L2 = normalize(vec3(0.55, -0.55, 0.62));
  col += uRim * pow(max(dot(N, normalize(L2 + V)), 0.0), 40.0) * 0.08;

  // Thinking shimmer: soft glints drifting through the light.
  if (uShimmer > 0.001) col += acc * uShimmer * 0.3 * smoothstep(0.6, 0.95, snoise(orbTwist(vObj) * 5.0 + vec3(0.0, t * 1.2, 0.0)));

  float luma = dot(col, vec3(0.2126, 0.7152, 0.0722));
  col = mix(vec3(luma), col, uSaturation);
  col *= uBrightness;
  // Gentle highlight shoulder instead of filmic tone mapping (keeps the palette pure).
  col = col * 1.22 / (1.0 + 0.22 * col);
  gl_FragColor = vec4(col, 1.0);
  #include <colorspace_fragment>
}
`;

export const HALO_VERTEX = /* glsl */ `
varying vec2 vQuad;
void main() {
  vQuad = position.xy;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

/**
 * Halo: a soft aura that takes the palette colours around the silhouette
 * (slowly turning), plus up to four ripple rings. Premultiplied "over"
 * blending, so it reads as a glow on dark stages and as a tinted aura on light
 * ones.
 */
export const HALO_FRAGMENT = /* glsl */ `
uniform vec3 uGlowA;
uniform vec3 uGlowB;
uniform vec3 uGlowC;
uniform vec3 uRingColor;
uniform float uGlow;
uniform float uSpread;
uniform float uPhase;
uniform vec4 uRingAge;
uniform vec4 uRingStrength;
varying vec2 vQuad;

float ring(float r, float age, float strength) {
  if (strength <= 0.0) return 0.0;
  float R = 1.02 + age * 0.5 * uSpread;
  float w = 0.018 + age * 0.07;
  float fade = pow(1.0 - clamp(age / 1.6, 0.0, 1.0), 2.0) * smoothstep(0.0, 0.08, age);
  return exp(-pow((r - R) / w, 2.0)) * fade * strength;
}

void main() {
  float r = length(vQuad);
  float d = max(r - 1.0, 0.0) / uSpread;
  float ang = atan(vQuad.y, vQuad.x);
  float wa = 0.5 + 0.5 * cos(ang - uPhase);
  float wb = 0.5 + 0.5 * cos(ang + uPhase * 0.7 + 2.2);
  vec3 tint = (uGlowA * (0.35 + wa) + uGlowB * (0.25 + wb) + uGlowC * 0.5) / (1.1 + wa + wb);
  // Close, soft falloff; gone well before the quad's edge.
  float halo = (exp(-d * 2.6) * 0.65 + exp(-d * 8.0) * 0.35) * smoothstep(0.9, 1.0, r) * smoothstep(2.6, 1.7, r);
  float a = halo * uGlow * 0.62;
  float rings = ring(r, uRingAge.x, uRingStrength.x) + ring(r, uRingAge.y, uRingStrength.y) + ring(r, uRingAge.z, uRingStrength.z) + ring(r, uRingAge.w, uRingStrength.w);
  float ra = clamp(rings * 0.32, 0.0, 1.0);
  float alpha = clamp(a + ra, 0.0, 1.0);
  // Straight colour through the output transfer, premultiplied afterwards.
  gl_FragColor = vec4((tint * a + uRingColor * ra) / max(a + ra, 1e-4), 1.0);
  #include <colorspace_fragment>
  gl_FragColor = vec4(gl_FragColor.rgb * alpha, alpha);
}
`;

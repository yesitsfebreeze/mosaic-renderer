#version 450

layout(location = 0) out vec4 outColor;

layout(std140, set = 0, binding = 1) uniform Fs_Params {
    vec4 eye;
    vec4 right;
    vec4 up;
    vec4 forward;
    vec4 sun;
    vec4 sky;
    vec4 level;
    vec4 screen;
};

layout(std430, set = 0, binding = 2) readonly buffer Scene { uint words[]; };

const float FAR_DISTANCE = 1e30;
const float SURFACE_OFFSET = 0.0002;

struct Span {
    float enter;
    float leave;
    vec3 enterNormal;
    vec3 leaveNormal;
};

struct Hit {
    float distance;
    vec3 normal;
    uint material;
};

uint sceneWord(uint index) {
    return words[index];
}

bool boxSpan(vec3 origin, vec3 direction, vec3 lower, vec3 upper, out Span span) {
    span.enter = -FAR_DISTANCE;
    span.leave = FAR_DISTANCE;
    span.enterNormal = vec3(0.0);
    span.leaveNormal = vec3(0.0);
    for (int axis = 0; axis < 3; ++axis) {
        if (direction[axis] == 0.0) {
            if (origin[axis] < lower[axis] || origin[axis] > upper[axis]) return false;
            continue;
        }
        float a = (lower[axis] - origin[axis]) / direction[axis];
        float b = (upper[axis] - origin[axis]) / direction[axis];
        float nearDistance = min(a, b);
        float farDistance = max(a, b);
        vec3 outward = vec3(0.0);
        outward[axis] = sign(direction[axis]);
        if (nearDistance > span.enter) {
            span.enter = nearDistance;
            span.enterNormal = -outward;
        }
        if (farDistance < span.leave) {
            span.leave = farDistance;
            span.leaveNormal = outward;
        }
        if (span.leave <= span.enter) return false;
    }
    return span.leave >= 0.0;
}

vec3 halfNormal(uint shape) {
    int code = int(shape) - 1;
    if (code >= 13) ++code;
    return vec3(code % 3 - 1, (code / 3) % 3 - 1, code / 9 - 1);
}

bool cellSpan(uint cell, ivec3 coordinate, vec3 origin, vec3 direction, out Span span) {
    uint material = cell & 255u;
    uint shape = (cell >> 8u) & 255u;
    if (material == 0u || shape > 26u) return false;

    if (!boxSpan(origin, direction, vec3(coordinate), vec3(coordinate + 1), span)) return false;
    if (shape != 0u) {
        vec3 normal = halfNormal(shape);
        float side = dot(normal, origin - vec3(coordinate) - 0.5);
        float slope = dot(normal, direction);
        if (slope == 0.0) {
            if (side > 0.0) return false;
        } else {
            float cut = -side / slope;
            if (slope < 0.0 && cut > span.enter) {
                span.enter = cut;
                span.enterNormal = normalize(normal);
            }
            if (slope > 0.0 && cut < span.leave) {
                span.leave = cut;
                span.leaveNormal = normalize(normal);
            }
        }
    }
    return span.leave > span.enter && span.leave >= 0.0;
}

bool trace(vec3 origin, vec3 direction, out Hit hit) {
    int size = int(level.x);
    Span bounds;
    if (!boxSpan(origin, direction, vec3(0.0), vec3(float(size)), bounds)) return false;
    float start = max(bounds.enter, 0.0);
    if (bounds.leave <= start) return false;

    vec3 position = origin + direction * start;
    ivec3 coordinate = ivec3(floor(position));
    ivec3 stepDirection = ivec3(sign(direction));
    vec3 nextBoundary = vec3(FAR_DISTANCE);
    for (int axis = 0; axis < 3; ++axis) {
        if (direction[axis] < 0.0 && position[axis] == floor(position[axis])) --coordinate[axis];
        coordinate[axis] = clamp(coordinate[axis], 0, size - 1);
        if (direction[axis] != 0.0) {
            int edge = coordinate[axis] + (direction[axis] > 0.0 ? 1 : 0);
            nextBoundary[axis] = (float(edge) - origin[axis]) / direction[axis];
        }
    }

    bool inside = false;
    // A ray crosses at most 3*N cell planes in this single dense chunk.
    for (int visit = 0; visit < 3 * size + 3; ++visit) {
        uint index = uint(coordinate.x + size * (coordinate.y + size * coordinate.z));
        uint cell = sceneWord(index);
        Span span;
        if (cellSpan(cell, coordinate, origin, direction, span)) {
            uint material = cell & 255u;
            if (inside) {
                if (material != hit.material || span.enter > hit.distance) return true;
            } else {
                hit.material = material;
                bool internalFace = false;
                if (span.enter == 0.0 && start == 0.0) {
                    ivec3 behind = ivec3(floor(origin));
                    for (int axis = 0; axis < 3; ++axis) {
                        if (direction[axis] > 0.0 && origin[axis] == floor(origin[axis])) --behind[axis];
                    }
                    if (all(greaterThanEqual(behind, ivec3(0))) && all(lessThan(behind, ivec3(size))) && any(notEqual(behind, coordinate))) {
                        uint previous = sceneWord(uint(behind.x + size * (behind.y + size * behind.z)));
                        Span previousSpan;
                        internalFace = (previous & 255u) == material && cellSpan(previous, behind, origin, direction, previousSpan)
                            && previousSpan.enter < 0.0 && previousSpan.leave == 0.0;
                    }
                }
                if (span.enter >= 0.0 && !internalFace) {
                    hit.distance = span.enter;
                    hit.normal = span.enterNormal;
                    return true;
                }
                inside = true;
            }
            // Continue through touching cells of the same material to its outer surface.
            hit.distance = span.leave;
            hit.normal = span.leaveNormal;
        } else if (inside) {
            return true;
        }

        float nextDistance = min(nextBoundary.x, min(nextBoundary.y, nextBoundary.z));
        if (inside && hit.distance < nextDistance) return true;
        if (nextDistance >= bounds.leave) break;
        for (int axis = 0; axis < 3; ++axis) {
            if (nextBoundary[axis] <= nextDistance) {
                coordinate[axis] += stepDirection[axis];
                if (coordinate[axis] < 0 || coordinate[axis] >= size) return inside;
                int edge = coordinate[axis] + (direction[axis] > 0.0 ? 1 : 0);
                nextBoundary[axis] = (float(edge) - origin[axis]) / direction[axis];
            }
        }
    }
    return inside;
}

vec4 materialVector(uint material, uint component) {
    uint index = uint(level.y) + material * 8u + component;
    return uintBitsToFloat(uvec4(sceneWord(index), sceneWord(index + 1u), sceneWord(index + 2u), sceneWord(index + 3u)));
}

void main() {
    vec2 uv = gl_FragCoord.xy / screen.xy;
    vec3 direction = normalize(forward.xyz + (uv.x * 2.0 - 1.0) * eye.w * right.w * right.xyz
                             + (1.0 - uv.y * 2.0) * eye.w * up.xyz);
    float scale = level.x / (2.0 * level.z);
    vec3 origin = (eye.xyz + level.z) * scale;
    Hit hit;
    if (!trace(origin, direction, hit)) {
        outColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    vec4 albedo = materialVector(hit.material, 0u);
    vec4 surface = materialVector(hit.material, 4u);
    vec3 lightDirection = normalize(sun.xyz);
    float diffuse = max(dot(hit.normal, lightDirection), 0.0);
    float visibility = 1.0;
    if (screen.z > 0.5 && diffuse > 0.0) {
        vec3 position = origin + direction * hit.distance + hit.normal * SURFACE_OFFSET;
        Hit blocker;
        if (trace(position, lightDirection, blocker)) visibility = 0.0;
    }
    float ambient = mix(0.16, 0.32, dot(hit.normal, normalize(sky.xyz)) * 0.5 + 0.5);
    vec3 colour = albedo.rgb * (ambient + diffuse * visibility * 0.85 + surface.z);
    colour = colour / (vec3(1.0) + colour);
    outColor = vec4(pow(max(colour, vec3(0.0)), vec3(1.0 / 2.2)), 1.0);
}

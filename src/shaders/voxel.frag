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
    uint state;
};

struct Run {
    vec3 lower;
    vec3 upper;
    uint material;
    uint state;
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

vec2 fixedPair(uint encoded) {
    return vec2(encoded & 65535u, encoded >> 16u) * (1.0 / 256.0);
}

uvec2 columnRuns(int axis, ivec2 coordinate, int size) {
    uint column = uint(axis * size * size + coordinate.x + size * coordinate.y);
    uint index = sceneWord(1u) + column * 2u;
    return uvec2(sceneWord(index), sceneWord(index + 1u));
}

Run loadRun(uint record, int axis, ivec2 coordinate) {
    uint index = sceneWord(2u) + record * 4u;
    vec2 depth = fixedPair(sceneWord(index));
    uint identity = sceneWord(index + 1u);
    vec2 lower = vec2(coordinate) + fixedPair(sceneWord(index + 2u));
    vec2 upper = vec2(coordinate) + fixedPair(sceneWord(index + 3u));
    int u = (axis + 1) % 3;
    int v = (axis + 2) % 3;
    Run run;
    run.lower[axis] = depth.x;
    run.upper[axis] = depth.y;
    run.lower[u] = lower.x;
    run.upper[u] = upper.x;
    run.lower[v] = lower.y;
    run.upper[v] = upper.y;
    run.material = identity & 255u;
    run.state = identity >> 8u;
    return run;
}

bool originRun(vec3 origin, vec3 direction, int axis, int size, out Hit hit) {
    if (any(lessThan(origin, vec3(0.0))) || any(greaterThan(origin, vec3(size)))) return false;
    ivec2 axes = ivec2((axis + 1) % 3, (axis + 2) % 3);
    ivec2 coordinate;
    for (int i = 0; i < 2; ++i) {
        int a = axes[i];
        coordinate[i] = int(floor(origin[a]));
        if (direction[a] > 0.0 && origin[a] == floor(origin[a])) --coordinate[i];
        if (coordinate[i] < 0 || coordinate[i] >= size) return false;
    }
    uvec2 column = columnRuns(axis, coordinate, size);
    for (uint i = 0u; i < column.y; ++i) {
        Run run = loadRun(column.x + i, axis, coordinate);
        Span span;
        if (run.material != 0u && boxSpan(origin, direction, run.lower, run.upper, span)
            && span.enter < 0.0 && span.leave >= 0.0) {
            hit.distance = span.leave;
            hit.normal = span.leaveNormal;
            hit.material = run.material;
            hit.state = run.state;
            return true;
        }
    }
    return false;
}

bool trace(vec3 origin, vec3 direction, out Hit hit) {
    int size = int(sceneWord(0u));
    vec3 magnitude = abs(direction);
    int axis = magnitude.y > magnitude.x ? 1 : 0;
    if (magnitude.z > magnitude[axis]) axis = 2;
    if (magnitude[axis] == 0.0) return false;
    Span bounds;
    if (!boxSpan(origin, direction, vec3(0.0), vec3(float(size)), bounds)) return false;
    float start = max(bounds.enter, 0.0);
    if (bounds.leave <= start) return false;

    vec3 position = origin + direction * start;
    ivec2 axes = ivec2((axis + 1) % 3, (axis + 2) % 3);
    ivec2 coordinate;
    ivec2 stepDirection;
    vec2 nextBoundary = vec2(FAR_DISTANCE);
    for (int i = 0; i < 2; ++i) {
        int a = axes[i];
        coordinate[i] = int(floor(position[a]));
        stepDirection[i] = int(sign(direction[a]));
        if (direction[a] < 0.0 && position[a] == floor(position[a])) --coordinate[i];
        coordinate[i] = clamp(coordinate[i], 0, size - 1);
        if (direction[a] != 0.0) {
            int edge = coordinate[i] + (direction[a] > 0.0 ? 1 : 0);
            nextBoundary[i] = (float(edge) - origin[a]) / direction[a];
        }
    }

    bool inside = start == 0.0 && originRun(origin, direction, axis, size, hit);
    float columnEnter = start;
    bool positive = direction[axis] > 0.0;
    // Depth advances by runs; only the two transverse grids need DDA crossings.
    for (int visit = 0; visit < 2 * size + 1; ++visit) {
        float columnLeave = min(bounds.leave, min(nextBoundary.x, nextBoundary.y));
        uvec2 column = columnRuns(axis, coordinate, size);
        for (uint i = 0u; i < column.y; ++i) {
            uint offset = positive ? i : column.y - 1u - i;
            Run run = loadRun(column.x + offset, axis, coordinate);
            float nearDepth = positive ? run.lower[axis] : run.upper[axis];
            float farDepth = positive ? run.upper[axis] : run.lower[axis];
            if ((farDepth - origin[axis]) / direction[axis] <= columnEnter) continue;
            if ((nearDepth - origin[axis]) / direction[axis] >= columnLeave) break;
            Span span;
            if (run.material == 0u || !boxSpan(origin, direction, run.lower, run.upper, span)
                || span.leave <= columnEnter || span.enter >= columnLeave) continue;
            if (inside) {
                if (run.material != hit.material || span.enter > hit.distance) return true;
            } else {
                hit.material = run.material;
                hit.state = run.state;
                if (span.enter >= 0.0) {
                    hit.distance = span.enter;
                    hit.normal = span.enterNormal;
                    return true;
                }
                inside = true;
            }
            hit.distance = span.leave;
            hit.normal = span.leaveNormal;
            hit.state = run.state;
        }

        if (inside && hit.distance < columnLeave) return true;
        if (columnLeave >= bounds.leave) break;
        columnEnter = columnLeave;
        for (int i = 0; i < 2; ++i) {
            if (nextBoundary[i] <= columnLeave) {
                int a = axes[i];
                coordinate[i] += stepDirection[i];
                if (coordinate[i] < 0 || coordinate[i] >= size) return inside;
                int edge = coordinate[i] + (direction[a] > 0.0 ? 1 : 0);
                nextBoundary[i] = (float(edge) - origin[a]) / direction[a];
            }
        }
    }
    return inside;
}

vec4 materialVector(uint material, uint component) {
    uint index = sceneWord(4u) + material * 8u + component;
    return uintBitsToFloat(uvec4(sceneWord(index), sceneWord(index + 1u), sceneWord(index + 2u), sceneWord(index + 3u)));
}

vec4 propertyVector(uint record, uint component) {
    uint index = sceneWord(8u) + record * 20u + 4u + component * 4u;
    return uintBitsToFloat(uvec4(sceneWord(index), sceneWord(index + 1u), sceneWord(index + 2u), sceneWord(index + 3u)));
}

bool findProperty(uint state, uint property, out uint record) {
    if (state >= sceneWord(7u)) return false;
    uint index = sceneWord(6u) + state * 2u;
    uint first = sceneWord(index);
    uint count = sceneWord(index + 1u);
    for (uint i = 0u; i < count; ++i) {
        record = first + i;
        if (sceneWord(sceneWord(8u) + record * 20u) == property) return true;
    }
    return false;
}

vec4 propertyValue(uint state, uint property, vec3 position, vec4 fallback) {
    uint record;
    if (!findProperty(state, property, record)) return fallback;
    return propertyVector(record, 0u) + propertyVector(record, 1u) * position.x
        + propertyVector(record, 2u) * position.y + propertyVector(record, 3u) * position.z;
}

vec4 propertyGradient(uint state, uint property, int axis) {
    uint record;
    if (axis < 0 || axis > 2 || !findProperty(state, property, record)) return vec4(0.0);
    return propertyVector(record, uint(axis + 1));
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
    vec3 position = origin + direction * hit.distance;
    albedo.rgb *= max(propertyValue(hit.state, 3u, position, vec4(1.0)).rgb, vec3(0.0));
    float temperature = propertyValue(hit.state, 1u, position, vec4(293.15)).x;
    float emission = max(propertyValue(hit.state, 2u, position, vec4(1.0)).x, 0.0);
    vec3 lightDirection = normalize(sun.xyz);
    float diffuse = max(dot(hit.normal, lightDirection), 0.0);
    float visibility = 1.0;
    if (screen.z > 0.5 && diffuse > 0.0) {
        Hit blocker;
        if (trace(position + hit.normal * SURFACE_OFFSET, lightDirection, blocker)) visibility = 0.0;
    }
    float ambient = mix(0.16, 0.32, dot(hit.normal, normalize(sky.xyz)) * 0.5 + 0.5);
    float heat = clamp((temperature - 700.0) / 1000.0, 0.0, 2.0);
    vec3 incandescence = vec3(1.0, 0.16 + 0.24 * heat, 0.025 + 0.1 * heat * heat) * heat * heat;
    vec3 colour = albedo.rgb * (ambient + diffuse * visibility * 0.85)
        + emission * (albedo.rgb * surface.z + incandescence);
    colour = colour / (vec3(1.0) + colour);
    outColor = vec4(pow(max(colour, vec3(0.0)), vec3(1.0 / 2.2)), 1.0);
}

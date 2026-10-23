// Détecte les intersections transverses des flèches dans un repère SVG commun.
// Les jonctions aux extrémités et les segments colinéaires sont exclus.
(svg) => {
    const epsilon = 1e-7;
    const endpointTolerance = 1; // Unité SVG ; évite de compter une jonction.
    const sampleStep = 5; // Unités SVG entre échantillons des courbes.
    const maxSamples = 250;
    const parallelTolerance = 1e-4; // Tolérance angulaire au bruit du moteur SVG.
    const cross = (a, b) => a.x * b.y - a.y * b.x;
    const subtract = (a, b) => ({x: a.x - b.x, y: a.y - b.y});
    const paths = [...svg.querySelectorAll('path.flowchart-link')].map(path => {
        const length = path.getTotalLength();
        const count = Math.max(1, Math.min(maxSamples, Math.ceil(length / sampleStep)));
        const transform = svg.getCTM().inverse().multiply(path.getCTM());
        const points = Array.from({length: count + 1}, (_, index) => {
            const point = path.getPointAtLength(length * index / count);
            return new DOMPoint(point.x, point.y).matrixTransform(transform);
        });
        return {id: path.id, points};
    });
    const nearEndpoint = (point, path) => [path.points[0], path.points.at(-1)].some(
        end => Math.hypot(point.x - end.x, point.y - end.y) <= endpointTolerance
    );
    const intersects = (first, second) => {
        for (let i = 1; i < first.points.length; i++) {
            const p = first.points[i - 1], endP = first.points[i];
            const r = subtract(endP, p);
            for (let j = 1; j < second.points.length; j++) {
                const q = second.points[j - 1], endQ = second.points[j];
                if (Math.max(p.x, endP.x) < Math.min(q.x, endQ.x) - epsilon ||
                    Math.max(q.x, endQ.x) < Math.min(p.x, endP.x) - epsilon ||
                    Math.max(p.y, endP.y) < Math.min(q.y, endQ.y) - epsilon ||
                    Math.max(q.y, endQ.y) < Math.min(p.y, endP.y) - epsilon) continue;
                const s = subtract(endQ, q), denominator = cross(r, s);
                if (Math.abs(denominator) <= parallelTolerance *
                    Math.hypot(r.x, r.y) * Math.hypot(s.x, s.y)) continue;
                const delta = subtract(q, p);
                const t = cross(delta, s) / denominator;
                const u = cross(delta, r) / denominator;
                if (t < -epsilon || t > 1 + epsilon || u < -epsilon || u > 1 + epsilon) continue;
                const point = {x: p.x + t * r.x, y: p.y + t * r.y};
                if (!nearEndpoint(point, first) && !nearEndpoint(point, second)) return true;
            }
        }
        return false;
    };
    const result = [];
    for (let i = 0; i < paths.length; i++) {
        for (let j = i + 1; j < paths.length; j++) {
            if (intersects(paths[i], paths[j])) result.push([paths[i].id, paths[j].id]);
        }
    }
    return result;
}

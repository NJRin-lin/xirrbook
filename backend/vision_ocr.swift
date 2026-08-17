#!/usr/bin/env swift
import Vision
import AppKit
import Foundation

guard CommandLine.arguments.count >= 2 else {
    print("Usage: vision_ocr.swift <image_path>")
    exit(1)
}

let imagePath = CommandLine.arguments[1]

// Load image: try NSImage first, then CGImageSource directly
var cgImage: CGImage? = nil
if let image = NSImage(contentsOfFile: imagePath),
   let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) {
    cgImage = cg
} else if let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: imagePath) as CFURL, nil),
          let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) {
    cgImage = cg
}

guard let image = cgImage else {
    fputs("ERROR: cannot load image: \(imagePath)\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
request.minimumTextHeight = 0.01  // detect small text

let handler = VNImageRequestHandler(cgImage: image, options: [:])

do {
    try handler.perform([request])
} catch {
    fputs("ERROR: Vision perform failed: \(error)\n", stderr)
    exit(1)
}

guard let observations = request.results, !observations.isEmpty else {
    fputs("ERROR: no text recognized\n", stderr)
    exit(1)
}

// Group observations into lines by y-coordinate, then sort lines top-to-bottom
// and tokens within each line left-to-right. Vision uses bottom-left origin.
let tolerance: CGFloat = 0.015

var used = Array(repeating: false, count: observations.count)
var lines: [[VNRecognizedTextObservation]] = []

for i in 0..<observations.count where !used[i] {
    used[i] = true
    let anchorY = observations[i].boundingBox.origin.y
    var line: [VNRecognizedTextObservation] = [observations[i]]

    for j in (i+1)..<observations.count where !used[j] {
        let y = observations[j].boundingBox.origin.y
        if abs(y - anchorY) <= tolerance {
            used[j] = true
            line.append(observations[j])
        }
    }
    lines.append(line)
}

// Sort lines: top-to-bottom (higher y first)
lines.sort { a, b in
    let ay = a.map { $0.boundingBox.origin.y }.max() ?? 0
    let by = b.map { $0.boundingBox.origin.y }.max() ?? 0
    return ay > by
}

// Sort tokens within each line: left-to-right
for line in lines {
    let sorted = line.sorted { $0.boundingBox.origin.x < $1.boundingBox.origin.x }
    let texts = sorted.compactMap { $0.topCandidates(1).first?.string }
    if !texts.isEmpty {
        print(texts.joined(separator: " "))
    }
}

#!/usr/bin/env swift
import Vision
import AppKit
import Foundation

guard CommandLine.arguments.count >= 2 else {
    print("Usage: vision_ocr.swift <image_path>")
    exit(1)
}

let imagePath = CommandLine.arguments[1]

guard let image = NSImage(contentsOfFile: imagePath),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    // Try loading as CGImage directly
    guard let directCG = CGImageSourceCreateWithURL(URL(fileURLWithPath: imagePath) as CFURL, nil),
          let cg = CGImageSourceCreateImageAtIndex(directCG, 0, nil) else {
        print("")
        exit(0)
    }
    let cgImage2 = cg
    recognize(cgImage2)
    exit(0)
}

recognize(cgImage)

func recognize(_ image: CGImage) {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    request.minimumTextHeight = 0.01  // detect small text

    let handler = VNImageRequestHandler(cgImage: image, options: [:])

    do {
        try handler.perform([request])
    } catch {
        print("")
        exit(0)
    }

    guard let observations = request.results else {
        print("")
        exit(0)
    }

    // Sort results top-to-bottom, left-to-right for natural reading order
    let sorted = observations.sorted { a, b in
        let ay = a.boundingBox.origin.y
        let by = b.boundingBox.origin.y
        if abs(ay - by) > 0.02 {
            return ay > by  // top-to-bottom (Vision uses bottom-left origin)
        }
        return a.boundingBox.origin.x < b.boundingBox.origin.x  // left-to-right
    }

    // Group text into lines (same y-coordinate)
    var lines: [[String]] = []
    var currentLine: [String] = []
    var lastY: CGFloat = -1

    for obs in sorted {
        guard let text = obs.topCandidates(1).first?.string else { continue }
        let y = obs.boundingBox.origin.y

        if let firstInLine = currentLine.first,
           let firstObs = sorted.first(where: { $0.topCandidates(1).first?.string == firstInLine }) {
            if abs(firstObs.boundingBox.origin.y - y) > 0.015 {
                lines.append(currentLine)
                currentLine = [text]
            } else {
                currentLine.append(text)
            }
        } else {
            currentLine.append(text)
        }
        lastY = y
    }
    if !currentLine.isEmpty {
        lines.append(currentLine)
    }

    // Output each line as space-separated tokens
    for line in lines {
        print(line.joined(separator: " "))
    }
}

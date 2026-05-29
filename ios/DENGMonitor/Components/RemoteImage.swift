import SwiftUI

/// Async image with skeleton + themed fallback on failure.
struct RemoteImage: View {
    let url: String?
    let fallbackSystemName: String
    let alt: String

    var body: some View {
        if let u = url, let imageURL = URL(string: u) {
            AsyncImage(url: imageURL) { phase in
                switch phase {
                case .empty:
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .success(let image):
                    image.resizable().scaledToFill()
                case .failure:
                    fallbackView
                @unknown default:
                    fallbackView
                }
            }
        } else {
            fallbackView
        }
    }

    private var fallbackView: some View {
        Image(systemName: fallbackSystemName)
            .font(.title2)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .accessibilityLabel(alt)
    }
}

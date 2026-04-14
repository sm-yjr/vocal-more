import SwiftUI

struct AddTermSheet: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    let onAdd: @MainActor (_ term: String, _ aliases: [String]) async throws -> Void

    @State private var term = ""
    @State private var aliasText = ""
    @State private var isSubmitting = false
    @State private var errorMessage: String?

    @FocusState private var termFocused: Bool

    var body: some View {
        VStack(spacing: 16) {
            Text("Add Dictionary Term")
                .font(.headline)

            TextField("Term (correct spelling)", text: $term)
                .textFieldStyle(.roundedBorder)
                .focused($termFocused)
                .disabled(isSubmitting)

            TextField("Aliases (comma-separated, optional)", text: $aliasText)
                .textFieldStyle(.roundedBorder)
                .disabled(isSubmitting)

            if let errorMessage {
                Text(errorMessage)
                    .font(.system(size: 12))
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            HStack {
                Button("Cancel") {
                    dismiss()
                }
                .buttonStyle(.bordered)
                .keyboardShortcut(.cancelAction)
                .disabled(isSubmitting)

                Button(isSubmitting ? "Adding..." : "Add") {
                    addEntry()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(isSubmitting || term.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding()
        .frame(width: 360)
        .onAppear {
            NSApp.activate(ignoringOtherApps: true)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                termFocused = true
            }
        }
    }

    private func addEntry() {
        let cleanTerm = term.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTerm.isEmpty, !isSubmitting else { return }

        let aliases = aliasText
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        isSubmitting = true
        errorMessage = nil

        Task {
            do {
                try await onAdd(cleanTerm, aliases)
                await MainActor.run {
                    appState.lastError = nil
                    isSubmitting = false
                    dismiss()
                }
            } catch {
                await MainActor.run {
                    let message = "Failed to add dictionary term: \(error.localizedDescription)"
                    appState.lastError = message
                    errorMessage = message
                    isSubmitting = false
                }
            }
        }
    }
}

import { useRef } from "react";
import { InfrastructureDiagram } from "./components/InfrastructureDiagram";

export default function App() {
  const diagramRef = useRef<HTMLDivElement>(null);

  const handleExport = async () => {
    if (!diagramRef.current) return;

    try {
      // Dynamically import html-to-image
      const htmlToImage = await import("html-to-image");

      // Capture the diagram at 3x scale for high resolution
      const dataUrl = await htmlToImage.toJpeg(diagramRef.current, {
        pixelRatio: 3,
        backgroundColor: "#ffffff",
        quality: 0.95
      });

      const link = document.createElement("a");
      link.href = dataUrl;
      link.download = "architecture-hci.jpg";
      link.click();
    } catch (error) {
      console.error("Error exporting diagram:", error);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      {/* Export Button - Not part of the diagram */}
      <div className="max-w-7xl mx-auto mb-4 flex justify-end">
        <button
          onClick={handleExport}
          className="bg-[#1e3a8a] text-white px-6 py-2 rounded-lg hover:bg-[#1e40af] transition-colors shadow-sm"
        >
          Exporter en JPG
        </button>
      </div>

      {/* Diagram Container */}
      <div className="max-w-7xl mx-auto">
        <div ref={diagramRef}>
          <InfrastructureDiagram />
        </div>
      </div>
    </div>
  );
}

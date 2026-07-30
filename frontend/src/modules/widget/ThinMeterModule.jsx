import VUMeter from '../../components/VUMeter/VUMeter.jsx';

/** Widget's slim meter — real state arrives over the WebSocket in Task 3.9. */
export default function ThinMeterModule() {
  return <VUMeter state="idle" />;
}

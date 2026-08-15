import { Route, Routes } from 'react-router-dom';
import CreateRoomPage from './pages/CreateRoomPage';
import JoinPage from './pages/JoinPage';
import RoomPage from './pages/RoomPage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<CreateRoomPage />} />
      <Route path="/join/:roomId/:inviteToken" element={<JoinPage />} />
      <Route path="/room/:roomId" element={<RoomPage />} />
      <Route path="*" element={<CreateRoomPage />} />
    </Routes>
  );
}

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

namespace OASInputRecorder {
public class InputEvent {
    public long Id;
    public string Time, Profile, Kind, Duration, Target, File, Raw;
    public string Source="实时";
    public int X, Y;
    public int? EndX, EndY;
    public string[] Fields() { return new [] { Id.ToString(), Source, Time, Profile, Kind, X.ToString(), Y.ToString(),
        EndX.HasValue ? EndX.Value.ToString() : "", EndY.HasValue ? EndY.Value.ToString() : "", Duration, Target, File, Raw }; }
}
public static class Parser {
    static readonly Regex Line = new Regex(@"^(?<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[.,]\d+)\s*\|\s*control\.py:\d+\s*\|\s*INFO\s*\|\s*(?:\[(?<duration>[\d.]+)s\]\s*)?(?<kind>Click|Swipe|Drag)\s*\(\s*(?<x>-?\d+),\s*(?<y>-?\d+)\)\s*(?<tail>.*)$", RegexOptions.Compiled);
    static readonly Regex End = new Regex(@"^->\s*\(\s*(?<x>-?\d+),\s*(?<y>-?\d+)\)");
    public static InputEvent Parse(string line, string path) {
        Match m = Line.Match(line.TrimStart('\ufeff'));
        if (!m.Success) return null;
        int x,y; if (!int.TryParse(m.Groups["x"].Value, out x) || !int.TryParse(m.Groups["y"].Value, out y)) return null;
        string kind=m.Groups["kind"].Value, tail=m.Groups["tail"].Value.Trim();
        InputEvent e = new InputEvent { Time=m.Groups["time"].Value, X=x, Y=y, Kind=kind=="Click" ? "点击" : kind=="Swipe" ? "滑动" : "拖动",
            Duration=m.Groups["duration"].Value, Target="", File=path, Raw=line.TrimEnd(), Profile=Profile(path) };
        if (kind=="Click") { if (!tail.StartsWith("@")) return null; e.Target=tail.Substring(1).Trim(); }
        else { Match end=End.Match(tail); int ex,ey; if (!end.Success || !int.TryParse(end.Groups["x"].Value,out ex) || !int.TryParse(end.Groups["y"].Value,out ey)) return null; e.EndX=ex; e.EndY=ey; }
        return e;
    }
    public static string Profile(string path) { return Regex.Replace(Path.GetFileNameWithoutExtension(path), @"^\d{4}-\d{2}-\d{2}_", ""); }
}
public static class Csv {
    public const string Header="序号,来源,时间,配置,动作,X,Y,结束X,结束Y,调用耗时秒,目标,日志文件,原日志";
    public static string Encode(string s) {
        s=s ?? "";
        // Avoid spreadsheet formula execution when inspecting exported target labels.
        if(s.Length>0 && "=+@\t\r".IndexOf(s[0])>=0) s="'"+s;
        return "\""+s.Replace("\"","\"\"")+"\"";
    }
    public static string Row(InputEvent e) { return string.Join(",",e.Fields().Select(Encode)); }
}
public sealed class TailCursor {
    public long Position; public DateTime Creation; public Decoder Decoder=Encoding.UTF8.GetDecoder(); public string Pending="";
    public TailCursor(string path, bool fromEnd) { FileInfo f=new FileInfo(path); Position=fromEnd?f.Length:0; Creation=f.CreationTimeUtc; }
    public List<string> Read(string path) {
        List<string> lines=new List<string>();
        FileInfo info=new FileInfo(path);
        if(info.Length<Position || info.CreationTimeUtc!=Creation) { Position=0; Pending=""; Decoder.Reset(); Creation=info.CreationTimeUtc; }
        using(FileStream stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)) {
            stream.Seek(Position,SeekOrigin.Begin);
            byte[] bytes=new byte[65536]; int read=stream.Read(bytes,0,bytes.Length); Position+=read;
            if(read==0) return lines;
            char[] chars=new char[Encoding.UTF8.GetMaxCharCount(read)]; int count=Decoder.GetChars(bytes,0,read,chars,0,false);
            string text=Pending+new string(chars,0,count); int begin=0,index;
            while((index=text.IndexOf('\n',begin))>=0) { lines.Add(text.Substring(begin,index-begin).TrimEnd('\r')); begin=index+1; }
            Pending=text.Substring(begin);
            if(Pending.Length>131072) Pending="";
        }
        return lines;
    }
}
public sealed class MapView:Control {
    public List<InputEvent> TraceEvents=new List<InputEvent>(); public InputEvent Selected; public Image Background;
    public MapView() { DoubleBuffered=true; BackColor=Color.FromArgb(18,25,38); }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e); Graphics g=e.Graphics; g.SmoothingMode=SmoothingMode.AntiAlias;
        float scale=Math.Max(.01f,Math.Min((Width-28)/1280f,(Height-40)/720f));
        float ox=(Width-1280*scale)/2, oy=(Height-720*scale)/2;
        g.TranslateTransform(ox,oy); g.ScaleTransform(scale,scale);
        if(Background!=null) g.DrawImage(Background,0,0,1280,720);
        using(Pen grid=new Pen(Color.FromArgb(65,110,130),1/scale)) {
            for(int x=0;x<=1280;x+=160) g.DrawLine(grid,x,0,x,720);
            for(int y=0;y<=720;y+=120) g.DrawLine(grid,0,y,1280,y);
            g.DrawRectangle(grid,0,0,1280,720);
        }
        foreach(InputEvent ev in TraceEvents.Skip(Math.Max(0,TraceEvents.Count-250))) DrawEvent(g,ev,scale,false);
        if(Selected!=null) DrawEvent(g,Selected,scale,true);
        g.ResetTransform();
        using(Brush text=new SolidBrush(Color.LightSteelBlue)) g.DrawString("1280 × 720 · 点击点 / 滑动起止点示意（非逐帧轨迹）",Font,text,10,Height-23);
    }
    static void DrawEvent(Graphics g,InputEvent e,float scale,bool selected) {
        Color color=selected ? Color.FromArgb(255,214,99) : e.EndX.HasValue ? Color.FromArgb(140,72,212,225) : Color.FromArgb(150,108,151,255);
        float radius=(selected?6:3)/scale;
        using(Pen p=new Pen(color,(selected?2.5f:1.5f)/scale)) using(Brush b=new SolidBrush(color)) {
            if(e.EndX.HasValue) using(AdjustableArrowCap cap=new AdjustableArrowCap(4,5)) { p.CustomEndCap=cap; g.DrawLine(p,e.X,e.Y,e.EndX.Value,e.EndY.Value); }
            g.FillEllipse(b,e.X-radius,e.Y-radius,radius*2,radius*2);
            if(selected) using(Font font=new Font("Microsoft YaHei UI",11/scale)) g.DrawString("#"+e.Id+" ("+e.X+", "+e.Y+")",font,b,e.X+8/scale,e.Y+7/scale);
        }
    }
    protected override void Dispose(bool disposing) { if(disposing && Background!=null) Background.Dispose(); base.Dispose(disposing); }
}
public sealed class RecorderForm:Form {
    TextBox root=new TextBox(); ComboBox profiles=new ComboBox(); Button toggle=new Button();
    Label status=new Label(), summary=new Label(); MapView map=new MapView(); ListView table=new ListView();
    Timer timer=new Timer(); Dictionary<string,TailCursor> cursors=new Dictionary<string,TailCursor>(StringComparer.OrdinalIgnoreCase);
    Dictionary<string,TailCursor> imports=new Dictionary<string,TailCursor>(); List<InputEvent> events=new List<InputEvent>();
    StreamWriter journal; string journalPath="",logRoot=""; bool running=false; long total=0,clicks=0,swipes=0;
    string settings=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"oas-path.txt");
    public RecorderForm() {
        Text="OAS 操作记录器 · MuMu"; Size=new Size(1180,860); MinimumSize=new Size(900,650); StartPosition=FormStartPosition.CenterScreen;
        Font=new Font("Microsoft YaHei UI",9); BackColor=Color.FromArgb(245,247,251);
        TableLayoutPanel layout=new TableLayoutPanel { Dock=DockStyle.Fill,ColumnCount=1,RowCount=6,Padding=new Padding(14) };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute,43)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute,42));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute,31)); layout.RowStyles.Add(new RowStyle(SizeType.Percent,53));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent,47)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute,48)); Controls.Add(layout);
        TableLayoutPanel pathbar=new TableLayoutPanel { Dock=DockStyle.Fill,ColumnCount=3 };
        pathbar.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,78)); pathbar.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100)); pathbar.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,112));
        pathbar.Controls.Add(new Label { Text="OAS 目录",AutoSize=true,Margin=new Padding(0,7,0,0) },0,0); root.Dock=DockStyle.Fill; pathbar.Controls.Add(root,1,0);
        Button browse=Button("选择目录",delegate { using(FolderBrowserDialog d=new FolderBrowserDialog()) { d.Description="选择 OAS 安装目录（其中应有 log 文件夹）"; if(d.ShowDialog()==DialogResult.OK) { root.Text=d.SelectedPath; Connect(); } } }); pathbar.Controls.Add(browse,2,0); layout.Controls.Add(pathbar,0,0);
        FlowLayoutPanel tools=new FlowLayoutPanel { Dock=DockStyle.Fill,WrapContents=false };
        toggle.Text="开始记录"; toggle.Width=95; toggle.Height=30; toggle.Click+=delegate { if(running) Stop(); else Connect(); }; tools.Controls.Add(toggle);
        tools.Controls.Add(new Label { Text="配置",AutoSize=true,Margin=new Padding(8,8,3,0) }); profiles.DropDownStyle=ComboBoxStyle.DropDownList; profiles.Width=130; profiles.Items.Add("全部配置"); profiles.SelectedIndex=0;
        profiles.SelectedIndexChanged+=delegate { if(running) ResetCursors(); }; tools.Controls.Add(profiles);
        tools.Controls.Add(Button("导入历史日志",Import)); tools.Controls.Add(Button("导出 CSV",Export));
        tools.Controls.Add(Button("打开记录目录",delegate { System.Diagnostics.Process.Start(Path.GetDirectoryName(journalPath)); }));
        tools.Controls.Add(Button("载入截图底图",LoadImage)); layout.Controls.Add(tools,0,1);
        summary.Dock=DockStyle.Fill; summary.Text="正在初始化…"; layout.Controls.Add(summary,0,2);
        map.Dock=DockStyle.Fill; layout.Controls.Add(map,0,3);
        table.Dock=DockStyle.Fill; table.View=View.Details; table.FullRowSelect=true; table.GridLines=true; table.HideSelection=false;
        foreach(var col in new [] { "序号|65","时间|175","配置|95","动作|60","起点|100","终点|100","耗时秒|75","目标|250" }) { string[] a=col.Split('|'); table.Columns.Add(a[0],int.Parse(a[1])); }
        table.SelectedIndexChanged+=delegate { if(table.SelectedItems.Count>0) { map.Selected=(InputEvent)table.SelectedItems[0].Tag; map.Invalidate(); } }; layout.Controls.Add(table,0,4);
        status.Dock=DockStyle.Fill; status.AutoEllipsis=true; layout.Controls.Add(status,0,5);
        string recordDir=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"Records"); Directory.CreateDirectory(recordDir);
        journalPath=Path.Combine(recordDir,"OAS_"+DateTime.Now.ToString("yyyyMMdd_HHmmss_fff")+"_"+System.Diagnostics.Process.GetCurrentProcess().Id+".csv");
        journal=new StreamWriter(journalPath,false,new UTF8Encoding(true)); journal.AutoFlush=true; journal.WriteLine(Csv.Header);
        root.Text=File.Exists(settings)?File.ReadAllText(settings).Trim():@"C:\Users\12296\Desktop\yys\OAS\OnmyojiAutoScript-easy-install";
        timer.Interval=500; timer.Tick+=Poll; timer.Start(); Shown+=delegate { Connect(); };
        FormClosed+=delegate { timer.Stop(); timer.Dispose(); journal.Dispose(); };
    }
    static Button Button(string title,Action action) { Button b=new Button { Text=title,AutoSize=true,Height=30 }; b.Click+=delegate { try { action(); } catch(Exception e) { MessageBox.Show(e.Message,"操作未完成",MessageBoxButtons.OK,MessageBoxIcon.Warning); } }; return b; }
    string[] Files() { if(!Directory.Exists(logRoot)) return new string[0]; return Directory.GetFiles(logRoot,"*.txt").Where(f=>Regex.IsMatch(Path.GetFileName(f),@"^\d{4}-\d{2}-\d{2}_.+\.txt$") && Parser.Profile(f)!="api" && Parser.Profile(f)!="server").OrderBy(f=>f).ToArray(); }
    bool Matches(string path) { return profiles.SelectedIndex<=0 || Parser.Profile(path)==(string)profiles.SelectedItem; }
    void Connect() {
        try {
            string selected=Path.GetFullPath(root.Text.Trim().Trim('"')); logRoot=Directory.Exists(Path.Combine(selected,"log"))?Path.Combine(selected,"log"):selected;
            if(!Directory.Exists(logRoot) || (Path.GetFileName(logRoot)!="log" && !Directory.GetFiles(logRoot,"*.txt").Any())) throw new IOException("未找到日志目录，请选择 OAS 安装文件夹或 log 文件夹。");
            string previous=profiles.SelectedItem as string;
            running=false; imports.Clear(); profiles.Items.Clear(); profiles.Items.Add("全部配置"); foreach(string name in Files().Select(Parser.Profile).Distinct()) profiles.Items.Add(name); profiles.SelectedIndex=Math.Max(0,profiles.Items.IndexOf(previous ?? "全部配置"));
            ResetCursors(); File.WriteAllText(settings,selected); running=true; toggle.Text="暂停记录";
            status.Text="监测中，等待新的 OAS 操作。自动保存："+journalPath+"\n数据来自 OAS 日志；不代表游戏已响应。长按在现有日志中可能显示为点击。"; RefreshSummary();
        } catch(Exception e) { Stop(); status.Text=e.Message; }
    }
    void ResetCursors() { cursors.Clear(); foreach(string path in Files().Where(Matches)) cursors[path]=new TailCursor(path,true); }
    void Stop() { running=false; toggle.Text="开始记录"; status.Text="已暂停。重新开始时只记录之后的新操作。当前 CSV 已保存。"; }
    void Poll(object sender,EventArgs args) {
        table.BeginUpdate();
        try {
            int changes=0;
            if(running) foreach(string path in Files().Where(Matches)) {
                TailCursor c; if(!cursors.TryGetValue(path,out c)) { c=new TailCursor(path,false); cursors[path]=c; }
                foreach(string line in c.Read(path)) if(Add(Parser.Parse(line,path))) changes++;
            }
            foreach(string path in imports.Keys.ToArray()) {
                TailCursor c=imports[path]; foreach(string line in c.Read(path)) { InputEvent e=Parser.Parse(line,path); if(e!=null)e.Source="历史"; if(Add(e)) changes++; }
                if(c.Position>=new FileInfo(path).Length) { imports.Remove(path); if(imports.Count==0) status.Text="历史日志导入完成，记录已保存。点击开始记录可监测之后的新操作。"; }
            }
            if(changes>0) { RefreshSummary(); map.TraceEvents=events; map.Invalidate(); if(table.Items.Count>0) table.EnsureVisible(table.Items.Count-1); }
        } catch(Exception e) { Stop(); imports.Clear(); status.Text="记录已暂停："+e.Message+"。已保存的数据仍在 CSV 中。"; }
        finally { table.EndUpdate(); }
    }
    bool Add(InputEvent e) {
        if(e==null) return false;
        e.Id=++total; journal.WriteLine(Csv.Row(e)); events.Add(e); if(events.Count>10000) events.RemoveRange(0,1000);
        if(e.EndX.HasValue) swipes++; else clicks++;
        ListViewItem item=new ListViewItem(new [] { e.Id.ToString(),e.Time,e.Profile,e.Kind,"("+e.X+", "+e.Y+")",e.EndX.HasValue?"("+e.EndX+", "+e.EndY+")":"—",e.Duration,e.Target }); item.Tag=e; table.Items.Add(item);
        if(table.Items.Count>1000) table.Items.RemoveAt(0); return true;
    }
    void RefreshSummary() { summary.Text=string.Format("共 {0:N0} 条   ·   点击 {1:N0}   ·   滑动/拖动 {2:N0}    |    表格显示最近 1,000 条，图中显示最近 250 条，CSV 保留全部",total,clicks,swipes); }
    void Import() { using(OpenFileDialog d=new OpenFileDialog { Filter="OAS 日志 (*.txt)|*.txt",InitialDirectory=logRoot,Multiselect=true }) {
        if(d.ShowDialog()!=DialogResult.OK) return; Stop(); cursors.Clear(); foreach(string p in d.FileNames) imports[p]=new TailCursor(p,false); status.Text="正在导入历史日志。导入记录也会写入当前 CSV；完成后可重新开始实时记录。";
    } }
    void Export() { using(SaveFileDialog d=new SaveFileDialog { Filter="CSV 文件 (*.csv)|*.csv",FileName=Path.GetFileName(journalPath) }) {
        if(d.ShowDialog()!=DialogResult.OK) return; journal.Flush(); if(string.Equals(Path.GetFullPath(d.FileName),Path.GetFullPath(journalPath),StringComparison.OrdinalIgnoreCase)) return;
        File.Copy(journalPath,d.FileName,true); status.Text="已导出全部记录："+d.FileName;
    } }
    void LoadImage() { using(OpenFileDialog d=new OpenFileDialog { Filter="截图|*.png;*.jpg;*.jpeg;*.bmp" }) { if(d.ShowDialog()!=DialogResult.OK)return;
        using(Image original=Image.FromFile(d.FileName)) { Image previous=map.Background; map.Background=new Bitmap(original); if(previous!=null)previous.Dispose(); } map.Invalidate();
    } }
}
public static class Program {
    [STAThread] public static int Main(string[] args) {
        if(args.Length>0 && args[0]=="--self-test") return SelfTest.Run(args.Length>1?args[1]:null);
        try { Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false); Application.Run(new RecorderForm()); return 0; }
        catch(Exception e) { MessageBox.Show(e.ToString(),"OAS 记录器启动失败"); return 1; }
    }
}
public static class SelfTest {
    static void Check(bool value,string reason) { if(!value) throw new Exception(reason); }
    public static int Run(string report) {
        List<string> results=new List<string>(); string dir=Path.Combine(Path.GetTempPath(),"oas-recorder-test-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(dir);
        try {
            string prefix="2026-09-16 16:25:30.513 | control.py:0107 | INFO | "; string f=Path.Combine(dir,"2026-09-16_demo.txt");
            InputEvent click=Parser.Parse(prefix+"[0.16s] Click ( 674,  313) @ login_animation_center",f);
            Check(click!=null&&click.X==674&&click.Y==313&&click.Profile=="demo"&&click.Duration=="0.16","click"); results.Add("PASS click/time/duration/profile");
            InputEvent swipe=Parser.Parse(prefix+"[0.41s] Swipe ( 187, 203) -> ( 200, 612)",f);
            Check(swipe!=null&&swipe.EndX==200&&swipe.EndY==612,"swipe"); results.Add("PASS swipe endpoints");
            Check(Parser.Parse(prefix+"Drag (1,2) -> (3,4)",f)!=null,"drag");
            Check(Parser.Parse(prefix.Replace("control.py","script_task.py")+"Swipe (1,2) -> (3,4)",f)==null,"not dispatched");
            Check(Parser.Parse("HTTP {\"text\":\""+prefix+"Click (1,2) @ duplicate\"}",f)==null,"API duplicate"); results.Add("PASS reject task intent/API duplicate");
            Check(Parser.Parse(prefix+"Click (99999999999999999,2) @ bad",f)==null,"overflow"); results.Add("PASS malformed input");
            Check(Csv.Encode("=SUM(1,2)").StartsWith("\"'="),"csv formula"); Check(Csv.Encode("a\"b")=="\"a\"\"b\"","csv quote"); results.Add("PASS CSV escaping");
            File.WriteAllText(f,"old\n",new UTF8Encoding(false)); TailCursor tail=new TailCursor(f,true); Check(tail.Read(f).Count==0,"from end");
            File.AppendAllText(f,"part",new UTF8Encoding(false)); Check(tail.Read(f).Count==0,"partial"); File.AppendAllText(f,"ial\n",new UTF8Encoding(false)); Check(tail.Read(f).Single()=="partial","finish"); Check(tail.Read(f).Count==0,"duplicate"); results.Add("PASS tail/partial/no duplicate");
            File.WriteAllText(f,"new\n",new UTF8Encoding(false)); Check(tail.Read(f).Single()=="new","truncate"); results.Add("PASS truncation");
            byte[] chinese=Encoding.UTF8.GetBytes("中文\n"); File.WriteAllBytes(f,new byte[0]); tail=new TailCursor(f,false);
            using(FileStream s=new FileStream(f,FileMode.Append)) s.Write(chinese,0,2); Check(tail.Read(f).Count==0,"utf8 split");
            using(FileStream s=new FileStream(f,FileMode.Append)) s.Write(chinese,2,chinese.Length-2); Check(tail.Read(f).Single()=="中文","utf8 complete"); results.Add("PASS split UTF8");
            using(MapView view=new MapView()) using(Bitmap bmp=new Bitmap(640,400)) { view.Size=bmp.Size; view.TraceEvents.Add(click); view.TraceEvents.Add(swipe); view.Selected=swipe; view.DrawToBitmap(bmp,new Rectangle(0,0,640,400)); } results.Add("PASS canvas rendering");
            string actual=@"C:\Users\12296\Desktop\yys\OAS\OnmyojiAutoScript-easy-install\log\2026-09-16_tomoe.txt";
            if(File.Exists(actual)) { int actions=0,moves=0; using(StreamReader r=new StreamReader(actual)) { string line; while((line=r.ReadLine())!=null) { InputEvent ev=Parser.Parse(line,actual); if(ev!=null) { actions++; if(ev.EndX.HasValue)moves++; } } } Check(actions>0&&moves>0,"real log"); results.Add("PASS existing OAS log: "+actions+" actions, "+moves+" swipes/drags"); }
            using(RecorderForm form=new RecorderForm()) using(Bitmap bmp=new Bitmap(form.Width,form.Height)) { form.ShowInTaskbar=false; form.Opacity=0; form.Show(); Application.DoEvents(); form.DrawToBitmap(bmp,new Rectangle(Point.Empty,bmp.Size)); form.Close(); if(report!=null) bmp.Save(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(report)),"preview.png")); } results.Add("PASS full window construction/render");
            if(report!=null) File.WriteAllLines(report,results,new UTF8Encoding(true)); return 0;
        } catch(Exception e) { if(report!=null) File.WriteAllText(report,string.Join("\r\n",results)+"\r\nFAIL "+e); return 1; }
        finally { foreach(string file in Directory.GetFiles(dir)) File.Delete(file); Directory.Delete(dir); }
    }
}
}

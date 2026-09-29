using System.Collections.ObjectModel;
using System.Globalization;
using System.Management.Automation;
using System.Management.Automation.Host;
using System.Security;

namespace AnKuanHost;

internal sealed class ConsolePsHost : PSHost
{
    private readonly Guid _instanceId = Guid.NewGuid();
    private readonly ConsolePsHostUi _ui = new();

    internal int? ExitCode { get; private set; }

    public override Guid InstanceId => _instanceId;
    public override string Name => "TFD.AnKuan.InProcessHost";
    public override Version Version => new(1, 0, 0, 0);
    public override PSHostUserInterface UI => _ui;
    public override CultureInfo CurrentCulture => CultureInfo.CurrentCulture;
    public override CultureInfo CurrentUICulture => CultureInfo.CurrentUICulture;

    public override void SetShouldExit(int exitCode) => ExitCode = exitCode;
    public override void EnterNestedPrompt() => throw new NotSupportedException("Nested prompt is not supported.");
    public override void ExitNestedPrompt() => throw new NotSupportedException("Nested prompt is not supported.");
    public override void NotifyBeginApplication() { }
    public override void NotifyEndApplication() { }
}

internal sealed class ConsolePsHostUi : PSHostUserInterface
{
    private readonly ConsolePsRawUi _rawUi = new();
    public override PSHostRawUserInterface RawUI => _rawUi;

    public override string ReadLine() => Console.ReadLine() ?? string.Empty;

    public override SecureString ReadLineAsSecureString()
    {
        var value = new SecureString();
        while (true)
        {
            var key = Console.ReadKey(intercept: true);
            if (key.Key == ConsoleKey.Enter)
            {
                Console.WriteLine();
                break;
            }
            if (key.Key == ConsoleKey.Backspace)
            {
                if (value.Length > 0)
                {
                    value.RemoveAt(value.Length - 1);
                }
                continue;
            }
            value.AppendChar(key.KeyChar);
        }
        value.MakeReadOnly();
        return value;
    }

    public override void Write(string value) => Console.Write(value);
    public override void WriteLine(string value) => Console.WriteLine(value);
    public override void WriteLine() => Console.WriteLine();

    public override void Write(ConsoleColor foregroundColor, ConsoleColor backgroundColor, string value)
    {
        var oldFg = Console.ForegroundColor;
        var oldBg = Console.BackgroundColor;
        try
        {
            Console.ForegroundColor = foregroundColor;
            Console.BackgroundColor = backgroundColor;
            Console.Write(value);
        }
        finally
        {
            Console.ForegroundColor = oldFg;
            Console.BackgroundColor = oldBg;
        }
    }

    public override void WriteDebugLine(string message)
    {
        var old = Console.ForegroundColor;
        try { Console.ForegroundColor = ConsoleColor.DarkGray; Console.WriteLine(message); }
        finally { Console.ForegroundColor = old; }
    }

    public override void WriteErrorLine(string value)
    {
        var old = Console.ForegroundColor;
        try { Console.ForegroundColor = ConsoleColor.Red; Console.Error.WriteLine(value); }
        finally { Console.ForegroundColor = old; }
    }

    public override void WriteVerboseLine(string message)
    {
        var old = Console.ForegroundColor;
        try { Console.ForegroundColor = ConsoleColor.Cyan; Console.WriteLine(message); }
        finally { Console.ForegroundColor = old; }
    }

    public override void WriteWarningLine(string message)
    {
        var old = Console.ForegroundColor;
        try { Console.ForegroundColor = ConsoleColor.Yellow; Console.WriteLine(message); }
        finally { Console.ForegroundColor = old; }
    }

    public override void WriteProgress(long sourceId, ProgressRecord record)
    {
        if (!string.IsNullOrWhiteSpace(record.StatusDescription))
            Console.WriteLine($"[{record.Activity}] {record.StatusDescription}");
    }

    public override Dictionary<string, PSObject> Prompt(string caption, string message, Collection<FieldDescription> descriptions)
    {
        if (!string.IsNullOrWhiteSpace(caption)) Console.WriteLine(caption);
        if (!string.IsNullOrWhiteSpace(message)) Console.WriteLine(message);

        var result = new Dictionary<string, PSObject>(StringComparer.OrdinalIgnoreCase);
        foreach (var field in descriptions)
        {
            Console.Write($"{field.Label}: ");
            var input = Console.ReadLine() ?? string.Empty;
            result[field.Name] = PSObject.AsPSObject(input);
        }
        return result;
    }

    public override int PromptForChoice(string caption, string message, Collection<ChoiceDescription> choices, int defaultChoice)
    {
        if (!string.IsNullOrWhiteSpace(caption)) Console.WriteLine(caption);
        if (!string.IsNullOrWhiteSpace(message)) Console.WriteLine(message);
        for (int i = 0; i < choices.Count; i++)
            Console.WriteLine($"[{i}] {choices[i].Label}");
        Console.Write($"選擇 [{defaultChoice}]: ");
        var input = Console.ReadLine();
        return int.TryParse(input, out var index) && index >= 0 && index < choices.Count ? index : defaultChoice;
    }

    public override PSCredential PromptForCredential(string caption, string message, string userName, string targetName)
        => PromptForCredential(caption, message, userName, targetName, PSCredentialTypes.Default, PSCredentialUIOptions.Default);

    public override PSCredential PromptForCredential(
        string caption,
        string message,
        string userName,
        string targetName,
        PSCredentialTypes allowedCredentialTypes,
        PSCredentialUIOptions options)
    {
        if (!string.IsNullOrWhiteSpace(caption)) Console.WriteLine(caption);
        if (!string.IsNullOrWhiteSpace(message)) Console.WriteLine(message);
        Console.Write($"使用者 [{userName}]: ");
        var user = Console.ReadLine();
        if (string.IsNullOrWhiteSpace(user)) user = userName;
        Console.Write("密碼: ");
        var password = ReadLineAsSecureString();
        return new PSCredential(user ?? string.Empty, password);
    }
}

internal sealed class ConsolePsRawUi : PSHostRawUserInterface
{
    public override ConsoleColor BackgroundColor
    {
        get => Console.BackgroundColor;
        set => Console.BackgroundColor = value;
    }

    public override Size BufferSize
    {
        get => new(Console.BufferWidth, Console.BufferHeight);
        set => Console.SetBufferSize(value.Width, value.Height);
    }

    public override Coordinates CursorPosition
    {
        get => new(Console.CursorLeft, Console.CursorTop);
        set => Console.SetCursorPosition(value.X, value.Y);
    }

    public override int CursorSize
    {
        get => Console.CursorSize;
        set => Console.CursorSize = value;
    }

    public override ConsoleColor ForegroundColor
    {
        get => Console.ForegroundColor;
        set => Console.ForegroundColor = value;
    }

    public override bool KeyAvailable => Console.KeyAvailable;
    public override Size MaxPhysicalWindowSize => new(Console.LargestWindowWidth, Console.LargestWindowHeight);
    public override Size MaxWindowSize => new(Console.LargestWindowWidth, Console.LargestWindowHeight);

    public override Coordinates WindowPosition
    {
        get => new(Console.WindowLeft, Console.WindowTop);
        set => Console.SetWindowPosition(value.X, value.Y);
    }

    public override Size WindowSize
    {
        get => new(Console.WindowWidth, Console.WindowHeight);
        set => Console.SetWindowSize(value.Width, value.Height);
    }

    public override string WindowTitle
    {
        get => Console.Title;
        set => Console.Title = value;
    }

    public override void FlushInputBuffer()
    {
        while (Console.KeyAvailable) Console.ReadKey(intercept: true);
    }

    public override BufferCell[,] GetBufferContents(Rectangle rectangle)
        => throw new NotSupportedException("Console buffer read is not required by this application.");

    public override KeyInfo ReadKey(ReadKeyOptions options)
    {
        bool noEcho = (options & ReadKeyOptions.NoEcho) != 0;
        var key = Console.ReadKey(intercept: noEcho);
        var states = ControlKeyStates.None;
        if ((key.Modifiers & ConsoleModifiers.Shift) != 0) states |= ControlKeyStates.ShiftPressed;
        if ((key.Modifiers & ConsoleModifiers.Alt) != 0) states |= ControlKeyStates.LeftAltPressed;
        if ((key.Modifiers & ConsoleModifiers.Control) != 0) states |= ControlKeyStates.LeftCtrlPressed;
        return new KeyInfo((int)key.Key, key.KeyChar, states, keyDown: true);
    }

    public override void ScrollBufferContents(Rectangle source, Coordinates destination, Rectangle clip, BufferCell fill)
        => throw new NotSupportedException("Console buffer scrolling is not required by this application.");

    public override void SetBufferContents(Coordinates origin, BufferCell[,] contents)
        => throw new NotSupportedException("Console buffer write is not required by this application.");

    public override void SetBufferContents(Rectangle rectangle, BufferCell fill)
        => throw new NotSupportedException("Console buffer write is not required by this application.");
}
